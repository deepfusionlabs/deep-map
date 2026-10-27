#!/usr/bin/env python3
"""Python import resolution by IMPORT ROOT — never by suffix (receiver-calls design 3.1; slice S3b).

`import a.b` names `<root>/a/b.py` or `<root>/a/b/__init__.py` for some IMPORT ROOT of the importer:

  * the repository root;
  * `src/`, when the tree has one;
  * the parent of every top-level package (the highest directory of a chain of directories that each hold an
    `__init__.py`);
  * the importer's OWN directory — only when the importer is not inside a package (script semantics:
    `sys.path[0]` is the script's directory). A module inside a package never sees its siblings by a bare name:
    Python 3 has no implicit relative import.

Within one root Python's own precedence applies (a package directory, then a module file, then a namespace
directory). Across roots the order is not knowable from the tree (sys.path, installs, the working directory),
so two DIFFERENT regular matches are AMBIGUOUS, never a guess. A namespace package (a directory without
`__init__.py`) combines every root that has that directory, as PEP 420 does, and only when no root has a regular
package or module of that name. A candidate that exists on disk but is not one of the given (graph) files makes
the answer UNKNOWN: the module is real, it is just not in the graph — never "external".

Relative imports (`from .x import y`, `from .. import z`) resolve by the importer's package PATH; climbing above
the repository root is UNKNOWN.

This replaces, for its callers, graphify_extract._py_module_index's suffix match, which maps the standard
library's `json` onto a project's `flask/json/` package (`import json` inside src/flask/json/provider.py is the
standard library: the importer is inside a package, so its own directory is no root, and no root holds a top-level
`json`). What is NOT a project module is decided by the caller (typed_calls' conservative external test also
requires that the top-level name be the stem of no project file or directory).

Stdlib only; pure apart from optional `os.path` existence checks under `root` (memoised). Deterministic.

    r = ImportRoots(files, root)                  # files: root-relative POSIX paths of the graph's Python files
    r.roots("pkg/mod.py")                         # -> ("", "src", ...)
    r.module("a.b", "pkg/mod.py")                 # -> Target("file", "src/a/b.py") | namespace | none | ...
    r.module("x", "pkg/sub/mod.py", level=2)      # from ..x import ...
    r.submodule(target, "c")                      # a.b.c given the Target of a.b
"""
from __future__ import annotations

import os
import posixpath
from typing import NamedTuple

PY_EXTS = (".py", ".pyi")
_INIT = ("__init__.py", "__init__.pyi")


class Target(NamedTuple):
    """What an import names. kind: file (`rel`) | namespace (`dirs`: the root-relative directories that make up
    a PEP 420 package, sorted) | none (no root has it: maybe external) | ambiguous (two different regular
    matches) | unknown (on disk but not in the graph; an invalid relative import; a bad name)."""
    kind: str
    rel: str = ""
    dirs: tuple = ()


T_NONE = Target("none")
T_AMBIGUOUS = Target("ambiguous")
T_UNKNOWN = Target("unknown")


def _norm(p) -> str:
    s = str(p or "").replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    return s.strip("/")


def _join(*parts) -> str:
    return "/".join(p for p in parts if p)


def _ident(s: str) -> bool:
    return bool(s) and s.isidentifier()


class ImportRoots:
    """The import roots of one tree and the resolution of absolute / relative module names against them."""

    def __init__(self, files, root=None):
        self.files = frozenset(f for f in (_norm(x) for x in files or ()) if f.lower().endswith(PY_EXTS))
        self.root = str(root) if root is not None else None
        self._disk: dict = {}
        self._memo: dict = {}
        dirs = set()
        for f in self.files:
            d = posixpath.dirname(f)
            while d:
                if d in dirs:
                    break
                dirs.add(d)
                d = posixpath.dirname(d)
        self.dirs = frozenset(dirs)               # every directory holding a graph Python file (at any depth)
        self._pkg: dict = {}
        roots = {""}
        if "src" in self.dirs or self._isdir("src"):
            roots.add("src")
        for d in sorted(self.dirs):
            if not self.is_package(d):
                continue
            top = d
            while posixpath.dirname(top) and self.is_package(posixpath.dirname(top)):
                top = posixpath.dirname(top)
            roots.add(posixpath.dirname(top))
        self.base_roots = tuple(sorted(roots))

    # ---------------------------------------------------------------- the tree
    def _exists(self, rel: str) -> bool:
        """`rel` is a file on disk under root (memoised; False without a root)."""
        if self.root is None or not rel:
            return False
        if rel not in self._disk:
            try:
                self._disk[rel] = os.path.isfile(os.path.join(self.root, *rel.split("/")))
            except (OSError, ValueError):
                self._disk[rel] = False
        return self._disk[rel]

    def _isdir(self, rel: str) -> bool:
        if self.root is None or not rel:
            return False
        key = rel + "/"
        if key not in self._disk:
            try:
                self._disk[key] = os.path.isdir(os.path.join(self.root, *rel.split("/")))
            except (OSError, ValueError):
                self._disk[key] = False
        return self._disk[key]

    def is_package(self, d: str) -> bool:
        """Directory `d` holds an `__init__.py` (or `.pyi`) — in the graph, or on disk."""
        if d not in self._pkg:
            self._pkg[d] = bool(d) and any(_join(d, i) in self.files or self._exists(_join(d, i)) for i in _INIT)
        return self._pkg[d]

    def roots(self, importer) -> tuple:
        """The import roots an ABSOLUTE import in `importer` searches (sorted; deterministic)."""
        d = posixpath.dirname(_norm(importer))
        if d in self.base_roots or self.is_package(d):
            return self.base_roots
        return tuple(sorted(set(self.base_roots) | {d}))

    # ---------------------------------------------------------------- one directory
    def _at(self, base: str):
        """What dotted path `base` (a root-relative path without extension) is in ONE directory of the search:
        ("file", rel) | ("disk", rel) (exists, not in the graph) | ("dir", base) (a namespace portion) | None."""
        for init in _INIT:                         # a regular package wins over a module of the same name
            p = _join(base, init)
            if p in self.files:
                return ("file", p)
        for init in _INIT:
            p = _join(base, init)
            if self._exists(p):
                return ("disk", p)
        for ext in PY_EXTS:
            p = base + ext
            if p in self.files:
                return ("file", p)
        for ext in PY_EXTS:
            p = base + ext
            if self._exists(p):
                return ("disk", p)
        if base in self.dirs:
            return ("dir", base)
        return None

    @staticmethod
    def _combine(hits) -> Target:
        regular = sorted({h[1] for h in hits if h[0] == "file"})
        disk = [h for h in hits if h[0] == "disk"]
        if len(regular) > 1:
            return T_AMBIGUOUS
        if disk:
            return T_UNKNOWN                       # a real module the graph does not hold (or holds elsewhere)
        if regular:
            return Target("file", regular[0])
        dirs = tuple(sorted({h[1] for h in hits if h[0] == "dir"}))
        return Target("namespace", dirs=dirs) if dirs else T_NONE

    # ---------------------------------------------------------------- resolution
    def module(self, dotted, importer, level: int = 0) -> Target:
        """The module `dotted` names for `importer` (level = the number of leading dots of a relative import;
        `dotted` may then be empty: `from . import x` names the package itself)."""
        dotted = str(dotted or "")
        parts = dotted.split(".") if dotted else []
        if any(not _ident(p) for p in parts):
            return T_UNKNOWN
        try:
            level = int(level)
        except (TypeError, ValueError):
            return T_UNKNOWN
        mk = (dotted, _norm(importer) if level else self.roots(importer), level)
        if mk in self._memo:
            return self._memo[mk]
        if level > 0:
            pkg = posixpath.dirname(_norm(importer))
            for _ in range(level - 1):
                if not pkg:
                    self._memo[mk] = T_UNKNOWN
                    return T_UNKNOWN               # `from ... import x` above the repository root
                pkg = posixpath.dirname(pkg)
            if not parts:
                hit = self._package_itself(pkg)
            else:
                hit = self._at(_join(pkg, *parts))
            out = self._combine([hit] if hit else [])
        elif not parts:
            out = T_UNKNOWN
        else:
            hits = [h for h in (self._at(_join(r, *parts)) for r in self.roots(importer)) if h]
            out = self._combine(hits)
        self._memo[mk] = out
        return out

    def _package_itself(self, pkg: str):
        for init in _INIT:
            p = _join(pkg, init)
            if p in self.files:
                return ("file", p)
        for init in _INIT:
            if self._exists(_join(pkg, init)):
                return ("disk", _join(pkg, init))
        if pkg in self.dirs or not pkg:
            return ("dir", pkg)
        return None

    def submodule(self, target: Target, name) -> Target:
        """`<target>.<name>` as a MODULE (a package's submodule), or none. A plain module has no submodules."""
        name = str(name or "")
        if not _ident(name) or not isinstance(target, tuple):
            return T_UNKNOWN
        if target.kind == "file":
            base = posixpath.basename(target.rel)
            if base not in _INIT:
                return T_NONE
            dirs = (posixpath.dirname(target.rel),)
        elif target.kind == "namespace":
            dirs = tuple(target.dirs)
        else:
            return T_UNKNOWN
        return self._combine([h for h in (self._at(_join(d, name)) for d in dirs) if h])


def top_name(dotted) -> str:
    """The top-level name of an absolute module path (`os.path` -> `os`)."""
    return str(dotted or "").split(".", 1)[0]
