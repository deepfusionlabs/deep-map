#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap benchmark — DETERMINISTIC completeness scorer (the reproducible half of scoring).

`completeness_pct` is the HERO metric (plan §7.5): the fraction of a question's pinned, enumerable
`ground_truth_set` that the agent's answer actually enumerated. This module computes it with **NO
model and NO judgment** — a pure, deterministic, word-boundary-honest matcher — so the completeness
number is reproducible and can't be over-credited. (Correctness — claims-not-code-refuted — is the
SEPARATE blind LLM-judge half; see `score_correctness.py`.)

A ground_truth_set item is one of:
  - a bare symbol            "charge"            -> word-bounded match (NOT a substring of "charger")
  - a qualified symbol       "Svc.charge"        -> the exact dotted form, word-bounded
  - a file:line              "billing/pay.py:12" -> path+line (\\ -> /, #L12 -> :12; basename ok too)
  - a symbol@location        "charge@billing/pay.py:12" -> needs BOTH the location AND the symbol
  - an edge                  "a->b"              -> both endpoints word-bounded co-occur
  - an alias group           "x | RetryWorker | x-y" -> covered if ANY accepted surface form matches

No external dependencies; importable + side-effect-free.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

# A file:line token looks like "<path-with-/-or-extension>:<digits>" at the end.
_FILE_LINE_RE = re.compile(r"^(?P<file>.+?\.[A-Za-z0-9_]+|.+?/.+?):(?P<line>\d+)$")


def _normalize_answer(text: str) -> str:
    """Lowercase + canonicalize separators and line markers so surface variants compare equal:
    backslash -> /, `#L44`/`# l 44` -> `:44`, and whitespace collapsed."""
    t = (text or "").lower()
    t = t.replace("\\", "/")
    t = re.sub(r"#\s*l\s*(\d+)", r":\1", t)     # file.py#L44 -> file.py:44
    t = re.sub(r"\s+", " ", t)
    return t


def _word_present(token: str, answer_norm: str) -> bool:
    """True iff `token` appears in the answer as a whole token (word-bounded), case-insensitive.
    Guards against substring over-credit (e.g. 'auth' must not be credited by 'authorize')."""
    token = (token or "").strip().lower()
    if not token:
        return False
    return re.search(r"(?<![A-Za-z0-9_])" + re.escape(token) + r"(?![A-Za-z0-9_])", answer_norm) is not None


def _file_line_present(file_line: str, answer_norm: str) -> bool:
    """True iff a `path:line` (or its basename:line) appears in the normalized answer — BOUNDED so it
    can't over-credit the HERO metric: the line number is RIGHT-bounded (`pay.py:12` must NOT match
    `pay.py:120`, a different line) and the path is LEFT-bounded (`pay.py:12` must NOT match
    `mypay.py:12`, a different file). A plain substring check did both wrongly."""
    fl = file_line.strip().lower().replace("\\", "/")

    def _present(needle: str) -> bool:
        # left: not preceded by a path-name char (so 'mypay.py' doesn't satisfy 'pay.py'); a leading
        # '/' or whitespace/quote is fine. right: not followed by a digit (so ':12' != ':120').
        return re.search(r"(?<![A-Za-z0-9_])" + re.escape(needle) + r"(?!\d)", answer_norm) is not None

    if _present(fl):
        return True
    base = fl.rsplit("/", 1)[-1]
    return base != fl and _present(base)


def _atom_covered(item: str, answer_norm: str) -> bool:
    """Cover one NON-alias atom (no '|'). Dispatch by shape: rich `file:line — desc` / symbol@loc /
    edge / file:line / symbol."""
    s = item.strip()
    if not s:
        return False
    # Normalize the GT atom's line marker the SAME way the answer is (`#L44` -> `:44`, `\\` -> `/`), so
    # the ground-truth format is as forgiving as the answer's — a curator's GitHub-style `pay.py#L12`
    # still credits an answer's `pay.py:12`. Harmless for symbols/edges (they carry no `#L`/`\\`).
    s = re.sub(r"#\s*l\s*(\d+)", r":\1", s.replace("\\", "/"), flags=re.IGNORECASE)
    # RICH DESCRIPTIVE atom: "<file:line> <Symbol> (<human description>)" — the SHIPPED benchmark format
    # (every questions.yaml atom is `<file:line> — <Symbol> (<desc>)`). The LEADING file:line is the
    # structured locator; the trailing prose is human description that can itself contain '->', ':',
    # parens, a second '—' — none of which is a matchable token. Credit completeness on the leading
    # file:line and ignore the description. Fire ONLY when the first whitespace-delimited token is a
    # PURE file:line (no '@', so a `symbol@file:line` atom still routes to the stronger both-needed
    # path below) AND there is trailing text (a lone file:line atom keeps the existing file:line path).
    head, _, _tail = s.partition(" ")
    if _tail and "@" not in head and _FILE_LINE_RE.match(head):
        return _file_line_present(head, answer_norm)
    if "@" in s:                                  # symbol@file:line — strongest: needs BOTH
        sym, _, loc = s.partition("@")
        return _file_line_present(loc, answer_norm) and _word_present(sym, answer_norm)
    if "->" in s:                                 # edge a->b — both endpoints co-occur
        a, _, b = s.partition("->")
        return _word_present(a, answer_norm) and _word_present(b, answer_norm)
    if _FILE_LINE_RE.match(s.replace("\\", "/")):  # file:line
        return _file_line_present(s, answer_norm)
    return _word_present(s, answer_norm)          # bare / qualified symbol


def item_covered(item: str, answer_norm: str) -> bool:
    """An item is covered if ANY of its pipe-separated accepted surface forms is covered."""
    forms = [f for f in (item or "").split("|")]
    if len(forms) > 1:
        return any(_atom_covered(f, answer_norm) for f in forms)
    return _atom_covered(item, answer_norm)


def score_completeness(answer_text: str, ground_truth_set: List[str]) -> Dict[str, Any]:
    """Return {completeness_pct, matched, missed, n, empty_oracle}. completeness_pct = the fraction of
    `ground_truth_set` enumerated in `answer_text`, deterministic. An empty oracle scores 0.0 + a flag
    (a missing completeness target is a wiring smell, never a free 100%)."""
    gts = list(ground_truth_set or [])
    if not gts:
        return {"completeness_pct": 0.0, "matched": [], "missed": [], "n": 0, "empty_oracle": True}
    answer_norm = _normalize_answer(answer_text)
    matched: List[str] = []
    missed: List[str] = []
    for item in gts:
        (matched if item_covered(item, answer_norm) else missed).append(item)
    pct = round(len(matched) / len(gts) * 100.0, 2)
    return {"completeness_pct": pct, "matched": matched, "missed": missed,
            "n": len(gts), "empty_oracle": False}
