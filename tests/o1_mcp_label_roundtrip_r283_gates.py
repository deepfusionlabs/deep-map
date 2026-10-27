#!/usr/bin/env python3
"""ISS-build:024 (C9) — ONE formatter, ONE parser for the project label a DeepMap MCP server announces.

Finding: provision proves which project an MCP listener serves by reading the free-text `instructions` its
`initialize` answer carries (mcp_bounds.project_instructions: "This DeepMap server indexes project <name> (<root>)
..." / "... embeds queries for project <label>; ..."). provision._label_names_project parsed that sentence with its
own substring checks and a regex — no shared parser, and no gate fed the formatter's output to the consumer. A
rewording on either side kept every gate green and made every real setup's MCP verify BLOCKED/FAILED; and the
substring checks answered wrong for real texts: a key-only label whose key merely STARTS with this project's key, or
another project's label whose path contains "(<this project's root>)", read as THIS project.

The fix: mcp_bounds.parse_project_instructions / parse_project_label — the inverse of project_instructions /
project_label, next to them and built from the same template constants — and provision._label_names_project reads the
label through it. This gate locks the round trip (roots with spaces, non-ASCII, parentheses, sentence punctuation,
Windows drive / UNC paths, a filesystem root, key-only labels, another project's label) and the consumer's answers.

Hermetic: temp DEEPMAP_HOME and project dirs; no MCP server, no Neo4j, no network.
Usage: python tests/o1_mcp_label_roundtrip_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import inspect
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []
_WIN = os.name == "nt"


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {str(detail)[:600]}"))


def _one_line(s: str) -> str:
    """What mcp_bounds.project_label does to a path before it goes between the parentheses (control chars -> ?)."""
    return "".join(c if c.isprintable() else "?" for c in str(s)).strip()


def _roots() -> list:
    common = ["/srv/shop (copy)/api (v2)", "/home/josé/проект", "/tmp/a. b; c/d. e", "/tmp/x (1)", "/",
              "/opt/tab\tin/path", "/p/" + "long-segment-" * 25 + "end"]
    if _WIN:
        return common + [r"C:\Users\John Doe\My Projects\shop api", r"C:\Users\José\проект",
                         r"C:\work\shop (copy)\api (v2)", r"D:\a. b; c\d. e", "C:\\", "C:/Users/x/proj",
                         r"\\server\share\proj (old)"]
    return common + [r"C:\Users\John Doe\My Projects\shop api"]


def _load(root: Path):
    for d in (root / "hooks", root / "build"):
        sys.path.insert(0, str(d))
    import mcp_bounds as MB  # noqa: E402
    import provision as P  # noqa: E402
    import port_registry as PR  # noqa: E402
    return MB, P, PR


def gate_a_round_trip(MB) -> None:
    print("\n-- A: parse_project_instructions(project_instructions(...)) gives back the project, for every root --")
    parse = getattr(MB, "parse_project_instructions", None)
    parse_label = getattr(MB, "parse_project_label", None)
    check("mcp_bounds.parse_project_instructions exists (the inverse of project_instructions)", callable(parse))
    check("mcp_bounds.parse_project_label exists (the inverse of project_label)", callable(parse_label))
    if not callable(parse) or not callable(parse_label):
        return
    for root in _roots():
        want = (_one_line(root), "")
        for server in ("search", "embed"):
            text = MB.project_instructions(server, env={"DEEPMAP_PROJECT_ROOT": root})
            got = parse(text)
            check(f"{server}: {root!r} round-trips", got == want, f"{got!r} from {text!r}")
        lab = MB.project_label(root)
        check(f"label: {root!r} round-trips", parse_label(lab) == want, f"{parse_label(lab)!r} from {lab!r}")
    key = "cccc1111dddd"
    for server in ("search", "embed"):
        text = MB.project_instructions(server, env={"DEEPMAP_PROJECT_KEY": key})
        check(f"{server}: a key-only label round-trips", parse(text) == ("", key), f"{parse(text)!r} from {text!r}")
        text = MB.project_instructions(server, env={"DEEPMAP_PROJECT_ROOT": "/srv/a", "DEEPMAP_PROJECT_KEY": key})
        check(f"{server}: root + key -> the root (project_label prefers it)", parse(text) == ("/srv/a", ""), repr(text))
    check("label 'with DeepMap key <k>' -> ('', k)", parse_label(f"with DeepMap key {key}") == ("", key))
    # a root whose basename looks like a key-only label is still a root
    odd = "/tmp/with DeepMap key abc"
    check("a root whose name reads like a key-only label parses as the root",
          parse(MB.project_instructions("search", env={"DEEPMAP_PROJECT_ROOT": odd})) == (odd, ""))


def gate_b_not_a_label(MB) -> None:
    print("\n-- B: a text that is not a DeepMap project announcement -> None --")
    parse = getattr(MB, "parse_project_instructions", None)
    if not callable(parse):
        check("mcp_bounds.parse_project_instructions exists", False)
        return
    lead = "This DeepMap server indexes project "
    for label, text in (("None", None), ("empty", ""), ("whitespace", "   "), ("plain prose", "hello world"),
                        ("the lead alone", lead), ("another sentence naming a project",
                                                   "The weather project (today) is done. Nothing else."),
                        ("a lead with no '<name> (<root>)' after it", lead + "nothing here. deepmap_search ..."),
                        ("a '<name> (<root>)' whose name is not the path's basename",
                         lead + "zeta (/srv/other/alpha). deepmap_search and deepmap_impact answer about that "
                                "project's code only.")):
        got = parse(text)
        check(f"{label} -> None", got is None, repr(got))
    # a future rewording of the sentence AFTER the label still reads the label (the lead is what anchors it)
    root = "/srv/shop (copy)/api"
    t = f"{lead}api ({root}). A later wording (with parentheses) of what follows; and more."
    check("a reworded tail after the label still yields the label's root", parse(t) == (root, ""), repr(parse(t)))
    t = "This DeepMap server embeds queries for project with DeepMap key abc123; a later wording."
    check("a reworded tail after a key-only label still yields the key", parse(t) == ("", "abc123"), repr(parse(t)))


def gate_c_formatter_unchanged(MB) -> None:
    print("\n-- C: the formatter's wording is unchanged (servers keep announcing the same text) --")
    root = r"C:\w\shop" if _WIN else "/w/shop"
    label = MB.project_label(root)
    check("project_label: '<name> (<root>)'", label == f"shop ({root})", repr(label))
    check("project_label: key-only 'with DeepMap key <k>'", MB.project_label("", "k1") == "with DeepMap key k1")
    check("project_label: nothing -> None", MB.project_label("", "") is None)
    s = MB.project_instructions("search", env={"DEEPMAP_PROJECT_ROOT": root})
    check("search instructions: the historical sentence, byte for byte",
          s == (f"This DeepMap server indexes project {label}. deepmap_search and deepmap_impact answer about that "
                f"project's code only. {MB._ELSEWHERE}"), repr(s))
    e = MB.project_instructions("embed", env={"DEEPMAP_PROJECT_ROOT": root})
    check("embed instructions: the historical sentence, byte for byte",
          e == f"This DeepMap server embeds queries for project {label}; it pairs with that project's deepmap_search.",
          repr(e))
    check("no project env -> no instructions", MB.project_instructions("search", env={}) is None)


def gate_d_consumer(MB, P, PR, work: Path) -> None:
    print("\n-- D: provision._label_names_project reads the label through the parser --")
    fn = getattr(P, "_label_names_project", None)
    check("provision._label_names_project exists", callable(fn))
    if not callable(fn):
        return
    src = inspect.getsource(fn)
    check("... and uses mcp_bounds.parse_project_instructions (one parser, no private regex)",
          "parse_project_instructions" in src, src[:300])
    proj = work / "my proj (v2)" / "api é"
    proj.mkdir(parents=True)
    target = str(proj)
    root = P._server_project_root(target)
    own_key = PR.project_key(target)

    def ins(server="search", **env):
        return MB.project_instructions(server, env=env)

    check("THIS project's search label -> True", fn(ins(DEEPMAP_PROJECT_ROOT=root), target) is True)
    check("THIS project's embed label -> True", fn(ins("embed", DEEPMAP_PROJECT_ROOT=root), target) is True)
    if _WIN:
        alt = root.upper().replace("\\", "/")
        check("THIS project's root spelled differently (case, slashes) -> True",
              fn(ins(DEEPMAP_PROJECT_ROOT=alt), target) is True, alt)
    check("key-only label with THIS project's key -> True", fn(ins(DEEPMAP_PROJECT_KEY=own_key), target) is True)
    check("key-only label with ANOTHER key -> False", fn(ins(DEEPMAP_PROJECT_KEY="0" * 12), target) is False)
    check("key-only label whose key merely STARTS with this project's key -> False (was a substring match)",
          fn(ins(DEEPMAP_PROJECT_KEY=own_key + "ff"), target) is False)
    other = work / "zeta"
    other.mkdir()
    check("ANOTHER project's label -> False", fn(ins(DEEPMAP_PROJECT_ROOT=str(other)), target) is False)
    sneaky = f"/elsewhere/q ({root.replace(chr(92), '/')})z"
    check("another project's label whose path CONTAINS '(<this root>)' -> False (was a substring match)",
          fn(ins(DEEPMAP_PROJECT_ROOT=sneaky), target) is False, sneaky)
    for label, text in (("no label (None)", None), ("garbage", "hello world"),
                        ("prose naming this root in parentheses", f"see ({root}) for details")):
        check(f"{label} -> False", fn(text, target) is False)

    print("\n-- D2: _mcp_label_problem names the other project's canonical label --")
    prob = getattr(P, "_mcp_label_problem", None)
    saved = P._probe_mcp_instructions
    try:
        for label, answer, want_none, needle in (
                ("this project's server", (True, ins(DEEPMAP_PROJECT_ROOT=root)), True, None),
                ("another project's server", (True, ins(DEEPMAP_PROJECT_ROOT=str(other))), False,
                 f"zeta ({other})"),
                ("a server with no label", (True, None), False, "WITHOUT a project label"),
                ("a server whose instructions are not a DeepMap label", (True, "hello world"), False, None),
                ("the probe could not tell", (False, None), True, None)):
            P._probe_mcp_instructions = lambda url, timeout=None, _a=answer: _a
            got = prob("deepmap-search", target)
            ok = (got is None) if want_none else (isinstance(got, str) and (needle is None or needle in got))
            check(f"_mcp_label_problem: {label} -> {'None' if want_none else 'a problem'}", ok, repr(got))
    finally:
        P._probe_mcp_instructions = saved


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
    print(f"DeepMap — one formatter, one parser for the MCP project label (root: {root})")
    print("=" * 72)
    for k in ("NEO4J_URI", "DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT", "DEEPMAP_PROJECT_ROOT", "DEEPMAP_PROJECT_KEY"):
        os.environ.pop(k, None)
    with tempfile.TemporaryDirectory(prefix="o1lbl") as td:
        home = Path(td) / "home"
        home.mkdir()
        os.environ["DEEPMAP_HOME"] = str(home)
        MB, P, PR = _load(root)
        gate_a_round_trip(MB)
        gate_b_not_a_label(MB)
        gate_c_formatter_unchanged(MB)
        work = Path(td) / "work"
        work.mkdir()
        gate_d_consumer(MB, P, PR, work.resolve())
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
