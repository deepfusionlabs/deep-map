#!/usr/bin/env python3
"""Python call-site reference resolver (design §8.2): every ast.Call under <corpus>, resolved with jedi goto().

MEASUREMENT ONLY. Run it under an isolated jedi 0.20.1 venv with -I (never the product's Python; jedi is in no
shipped list — tests/call_linking_harness_gates.py checks):
    uv venv <scratch>/venv-jedi && uv pip install --python <scratch>/venv-jedi "jedi==0.20.1" "parso==0.8.7"
    <scratch>/venv-jedi/Scripts/python -I benchmark/call_linking/truth_jedi.py <corpus> <out.json>

One row per call site: file, line, col, form, name, defstack and the in-repo definitions jedi resolves.
  form      bare | self | cls | super | name_attr (`x.m()`: a module/alias OR a local) | recv (`a.b.m()`,
            `f().m()`, `x[i].m()` ...) | other (`(f)()`, subscripts ...)
  defstack  the enclosing defs, outermost first: [qualname, def line, [decorator start line]] — a graph node
            may sit on either line (compare_calls tries both)
  defs      [{file, line, alt_lines, type, name, full_name}] — in-corpus definitions only (stdlib and
            site-packages targets are dropped: a row with no defs is unresolved or external)
Rows are sorted (file, line, col); the output carries no timing, so a re-run is byte-identical.

Sources are read the way the interpreter reads them: `ast.parse` on the file's BYTES (a UTF-8 BOM and a PEP 263
coding cookie are honoured) and the text handed to jedi decoded with the same encoding (tokenize.detect_encoding,
universal newlines). Columns are CHARACTER columns: ast reports UTF-8 byte offsets, jedi's goto() takes characters,
so a call after a non-ASCII character on its line is converted first (char_col) — else it misses or resolves the
wrong name. A file that does not parse is listed by name in `parse_errors` (its sites are not in the truth).

Reference resolvers are not truth (no override expansion; one name on two receivers can read as "refuted"):
the blind audit (audit_sample.py) decides precision, and a resolver error rate of 10% or more on the audit
sample voids that corpus's G deltas (design §8.4 A).
"""
from __future__ import annotations

import ast
import io
import json
import sys
import time
import tokenize
from pathlib import Path

WANT_JEDI = "0.20.1"
SKIP_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "dist", "graphify-out", ".deepmap",
             ".tox", ".nox", "__pycache__", ".mypy_cache", ".pytest_cache", ".eggs", "site-packages"}


def files(root: Path):
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]):
            continue
        yield p, rel.as_posix()


def read_source(p: Path):
    """(text, tree) of a Python file as the interpreter reads it: ast.parse on the bytes (BOM + coding cookie
    honoured), the text decoded with the encoding tokenize detects, newlines universal. Raises on a file that
    does not parse (SyntaxError / ValueError / UnicodeDecodeError)."""
    data = p.read_bytes()
    tree = ast.parse(data)
    enc = tokenize.detect_encoding(io.BytesIO(data).readline)[0]
    text = io.TextIOWrapper(io.BytesIO(data), encoding=enc).read()
    return text, tree


def char_col(lines, lineno: int, col_b: int) -> int:
    """ast's UTF-8 BYTE column on line `lineno` (1-based) of `lines` -> the CHARACTER column jedi takes."""
    if lines is None or not (1 <= lineno <= len(lines)):
        return col_b
    return len(lines[lineno - 1].encode("utf-8")[:col_b].decode("utf-8", errors="replace"))


def deco_line(node) -> "int | None":
    decos = getattr(node, "decorator_list", None) or []
    lines = [d.lineno for d in decos if getattr(d, "lineno", None)]
    return min(lines) if lines and min(lines) != node.lineno else None


class DefLines(ast.NodeVisitor):
    """def/class line -> its first decorator line (when decorated), for one file."""

    def __init__(self):
        self.alt = {}

    def _visit(self, node):
        d = deco_line(node)
        if d is not None:
            self.alt[node.lineno] = d
        self.generic_visit(node)

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _visit


class Sites(ast.NodeVisitor):
    """Every ast.Call with its form, name, defstack and jedi position. `lines` (the decoded source split into
    lines) turns ast's byte columns into character columns; without it the columns stay as ast gives them."""

    def __init__(self, lines=None):
        self.stack = []      # (name, lineno, kind, qualname, alt)
        self.calls = []
        self.lines = lines

    def _scope(self, node, kind):
        q = ".".join([s[0] for s in self.stack if s[2] in ("class", "def")] + [node.name])
        d = deco_line(node)
        self.stack.append((node.name, node.lineno, kind, q, [d] if d is not None else []))
        self.generic_visit(node)
        self.stack.pop()

    def visit_ClassDef(self, node):
        self._scope(node, "class")

    def visit_FunctionDef(self, node):
        self._scope(node, "def")

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node):
        f = node.func
        defs = [s for s in self.stack if s[2] == "def"]
        if isinstance(f, ast.Name):
            form, name, pos = "bare", f.id, (f.lineno, char_col(self.lines, f.lineno, f.col_offset))
        elif isinstance(f, ast.Attribute):
            v = f.value
            if isinstance(v, ast.Name) and v.id in ("self", "cls"):
                form = v.id
            elif isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "super":
                form = "super"
            elif isinstance(v, ast.Name):
                form = "name_attr"
            else:
                form = "recv"
            name = f.attr
            pos = (f.end_lineno, char_col(self.lines, f.end_lineno, f.end_col_offset) - len(f.attr))
        else:
            form, name, pos = "other", None, None
        self.calls.append({"line": node.lineno, "col": char_col(self.lines, node.lineno, node.col_offset),
                           "form": form, "name": name,
                           "defstack": [[d[3], d[1], d[4]] for d in defs], "pos": pos})
        self.generic_visit(node)


def main(argv=None) -> int:
    for s_ in (sys.stdout, sys.stderr):
        try:
            s_.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    argv = list(sys.argv[1:] if argv is None else argv)
    any_jedi = "--any-jedi" in argv
    argv = [a for a in argv if a != "--any-jedi"]
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        import jedi
        import parso
    except ImportError:
        print(f"[truth_jedi] jedi is not importable here — run me under the isolated reference venv: "
              f"uv venv <scratch>/venv-jedi && uv pip install --python <that python> \"jedi=={WANT_JEDI}\", then "
              f"<that python> -I {Path(__file__).name} <corpus> <out.json>", file=sys.stderr)
        return 2
    if jedi.__version__ != WANT_JEDI and not any_jedi:
        print(f"[truth_jedi] REFUSED: jedi {jedi.__version__} (the registered reference is {WANT_JEDI}; "
              f"--any-jedi to override, recorded in the output)", file=sys.stderr)
        return 2
    root, out = Path(argv[0]).resolve(), Path(argv[1])
    project = jedi.Project(path=str(root), added_sys_path=[str(root / "src"), str(root)])
    alt_cache: dict = {}

    def alt_lines(rel: str, line: int):
        if rel not in alt_cache:
            v = DefLines()
            try:
                v.visit(read_source(root / rel)[1])
            except Exception:  # noqa: BLE001
                pass
            alt_cache[rel] = v.alt
        a = alt_cache[rel].get(line)
        return [a] if a is not None else []

    rows, n_files, parse_errors, n_errors = [], 0, [], 0
    t0 = time.time()
    for p, rel in files(root):
        try:
            src, tree = read_source(p)
        except Exception as exc:  # noqa: BLE001
            parse_errors.append(f"{rel}: {type(exc).__name__}")
            continue
        n_files += 1
        v = Sites(src.split("\n"))
        v.visit(tree)
        script = jedi.Script(code=src, path=str(p), project=project)
        for c in v.calls:
            pos = c.pop("pos")
            c["file"] = rel
            c["defs"] = []
            if c["name"] is not None:
                try:
                    got = script.goto(*pos, follow_imports=True)
                except Exception as exc:  # noqa: BLE001
                    c["error"] = repr(exc)[:200]
                    n_errors += 1
                    got = []
                seen = set()
                for d in got:
                    mp = d.module_path
                    if mp is None or d.line is None:
                        continue
                    try:
                        drel = Path(mp).resolve().relative_to(root).as_posix()
                    except ValueError:
                        continue                      # outside the corpus (stdlib / site-packages)
                    if any(part in SKIP_DIRS for part in Path(drel).parts[:-1]):
                        continue
                    k = (drel, d.line, d.type)
                    if k in seen:
                        continue
                    seen.add(k)
                    c["defs"].append({"file": drel, "line": d.line, "alt_lines": alt_lines(drel, d.line),
                                      "type": d.type, "name": d.name, "full_name": d.full_name})
                c["defs"].sort(key=lambda d: (d["file"], d["line"], d["type"]))
            rows.append(c)
    rows.sort(key=lambda r: (r["file"], r["line"], r["col"], r.get("name") or ""))
    resolver = {"name": "jedi", "version": jedi.__version__, "parso": parso.__version__,
                "python": ".".join(str(x) for x in sys.version_info[:3]), "override": any_jedi}
    doc = {"schema": "call-linking/truth/1", "resolver": resolver, "root": root.as_posix(), "n_files": n_files,
           "n_parse_errors": len(parse_errors), "parse_errors": sorted(parse_errors), "n_resolver_errors": n_errors,
           "rows": rows}
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")
    print(f"[truth_jedi] files {n_files} (parse errors {len(parse_errors)}: {sorted(parse_errors)[:10]}) "
          f"call sites {len(rows)} resolved-in-corpus "
          f"{sum(1 for r in rows if r['defs'])} resolver errors {n_errors} in {time.time() - t0:.1f}s",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
