#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap intent-summary synthesis (Phase 3a) — richer summaries for UNDOCUMENTED code.

DeepMap embeds INTENT SUMMARIES, not raw code (DD-3). A docstring is the best intent signal, so
it always wins. But most real code is undocumented, and the old fallback was the bare
``"{kind} {name}"`` (e.g. "function _weighted_rrf") — almost no signal beyond the identifier.

This module synthesizes a summary from code STRUCTURE so the embedder has real intent tokens:
  * the **humanized name** (`_weighted_rrf` -> "weighted rrf", `DeepMapRetriever.search` ->
    "deep map retriever search") — the single strongest signal, split into matchable words;
  * **parameters** (drops self/cls) — they often name the domain ("rankings, weights, k");
  * **callees** — what the unit orchestrates ("calls verify connectivity, search"), with
    generic builtins filtered out so the signal isn't drowned;
  * for a class/module, its **members** (method / top-level definition names).

Pure + deterministic (ast in, str out): no Neo4j, no model, no network. Callee/member order is a
deterministic `ast.walk` order (stable per AST -> reproducible summaries), NOT source-line order.

This is the DEFAULT synthesizer and always runs. The OPT-IN LLM enrichment tier (build/enrich.py, off
by default) is a SEPARATE, opt-in pass that may OVERRIDE body_summary for undocumented nodes via a
gitignored sidecar; it never runs unless the user explicitly enables it, and deleting the sidecar
reverts to this deterministic output.
"""
from __future__ import annotations

import ast
import re

SUMMARY_MAX = 300  # matches the historical body_summary cap (sync.py)
_MAX_CALLEES = 6
_MAX_MEMBERS = 8

# Generic builtins that reveal no domain intent — excluded from the callee list so the
# meaningful calls aren't drowned. (Kept deliberately small: things like defaultdict, sorted
# are arguably informative, but these are pure plumbing.)
_NOISE_CALLEES = frozenset({
    "len", "str", "int", "float", "bool", "dict", "list", "tuple", "set", "frozenset",
    "print", "isinstance", "issubclass", "hasattr", "getattr", "setattr", "delattr",
    "range", "enumerate", "super", "type", "repr", "format", "min", "max", "sum",
    "map", "filter", "zip", "iter", "next", "abs", "any", "all", "id", "vars", "dir",
    "open", "input", "ord", "chr", "hex", "bin", "round", "sorted", "reversed",
})

# Split a single identifier segment into camel/number/lower runs:
#   "DeepMap" -> Deep, Map ;  "RRF" -> RRF ;  "ClassDef" -> Class, Def ;  "stage0" -> stage0
_WORD_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z][a-z0-9]+|[A-Z]+|[a-z0-9]+")


def humanize(name: str) -> str:
    """Turn an identifier into space-separated lowercase words (snake_case + CamelCase + dots)."""
    words: list[str] = []
    for part in name.replace(".", " ").split():
        for seg in part.split("_"):
            if not seg:
                continue
            words.extend(w.lower() for w in _WORD_RE.findall(seg))
    return " ".join(words)


def _params(node: ast.AST) -> list[str]:
    a = getattr(node, "args", None)
    if a is None:
        return []
    names = [p.arg for p in (list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs))]
    if a.vararg:
        names.append(a.vararg.arg)
    if a.kwarg:
        names.append(a.kwarg.arg)
    return [n for n in names if n not in ("self", "cls")]


def _callees(node: ast.AST) -> list[str]:
    """Distinct callee names within `node` in `ast.walk` order (NOT source-line), builtins filtered."""
    seen: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            f = child.func
            callee = getattr(f, "attr", None) or getattr(f, "id", None)
            if callee and callee not in _NOISE_CALLEES and callee not in seen:
                seen.append(callee)
    return seen


def _cap(text: str) -> str:
    return text[:SUMMARY_MAX].rstrip()


def summarize_callable(node: ast.AST, name: str, kind: str) -> str:
    """Synthesize a summary for an undocumented function/method/route."""
    base = f"{humanize(name)}: {kind.lower()}"
    params = _params(node)
    if params:
        base += " with " + ", ".join(params)
    callees = _callees(node)[:_MAX_CALLEES]
    if callees:
        base += "; calls " + ", ".join(humanize(c) for c in callees)
    return _cap(base)


def summarize_class(node: ast.ClassDef, name: str) -> str:
    methods = [n.name for n in node.body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and not n.name.startswith("__")]
    base = f"{humanize(name)}: class"
    if methods:
        base += " with methods " + ", ".join(humanize(m) for m in methods[:_MAX_MEMBERS])
    return _cap(base)


def summarize_module(tree: ast.Module, stem: str) -> str:
    defs = [n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    base = f"{humanize(stem)}: module"
    if defs:
        base += " defining " + ", ".join(humanize(d) for d in defs[:_MAX_MEMBERS])
    return _cap(base)


def summarize(node: ast.AST, name: str, kind: str, *, docstring: str | None = None) -> str:
    """Best intent summary for `node`: docstring first line if present, else synthesized.

    `kind` is the DeepMap label ("Function"/"Method"/"Route"/"Class"/"Module"). `docstring`
    may be passed explicitly; otherwise it's read from the node where applicable.
    """
    doc = docstring
    if doc is None and isinstance(
        node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)
    ):
        doc = ast.get_docstring(node)
    if doc and doc.strip():
        return _cap(doc.strip().split("\n")[0])
    if isinstance(node, ast.ClassDef):
        return summarize_class(node, name)
    if isinstance(node, ast.Module):
        return summarize_module(node, name)
    return summarize_callable(node, name, kind)
