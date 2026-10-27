#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the Claude Code PLUGIN PACKAGING surface (manifest + version coherence).

This locks the load-bearing packaging invariants that decide whether DeepMap installs as a real
plugin AND ships ONE coherent version (T16 / FD-4) — a surface that was previously unchecked by the
green gate (`run_all.py` only ran copy_honesty_scan's --selftest, never the real version scan).

What it enforces:
  * MANIFEST LOCATION — the manifest MUST be `.claude-plugin/plugin.json`, and a repo-ROOT
    `plugin.json` must NOT exist. Per the official docs ("Only plugin.json goes inside
    .claude-plugin/") a root-level plugin.json is SILENTLY IGNORED by plugin discovery, so a
    misplaced one is a green-install-but-dead-manifest trap.
  * MANIFEST SHAPE — valid JSON; required keys (name/version/description); name == 'deep-map';
    version is semver X.Y.Z.
  * MARKETPLACE — `.claude-plugin/marketplace.json` is valid JSON; carries the deep-map plugin entry
    with a source + version.
  * VERSION COHERENCE (T16) — plugin.json.version == marketplace deep-map.version == the latest
    RELEASED CHANGELOG heading == the `commands/version.md` loaded-version canary. ONE version in all
    FOUR places; a drift fails the gate. (Automates the manual "version coherent" check the
    implementation plan §8 asked for; the canary is what lets /deep-map:version detect a stale load.)
  * COMPONENTS — the components the manifest implies exist at the plugin root: commands/ (>= 4 .md) and
    skills/deep-map/SKILL.md.
  * NO SHIPPED .mcp.json — the plugin root must NOT ship a .mcp.json. A plugin's .mcp.json is loaded in EVERY
    project, and the one this repository used to ship named 8787/8788, the ports of whichever project was set up
    first: before setup /mcp listed two failed servers, in the first project the tools appeared twice, and in every
    other project they answered from the first project's graph. /deep-map:setup writes each project's own .mcp.json
    (provision.write_mcp_json; its content is gated in tests/mcp_transport_gates.py and tests/port_registry_gates.py).
    So .gitignore lists it, and where this tree is a git checkout, git must not track it. One state reads PENDING,
    not FAIL: git tracks it while the .gitignore line that stops shipping it is still an unstaged edit (the index's
    own .gitignore does not list it yet), so the commit that takes the line must also run `git rm --cached .mcp.json`.
    Once that .gitignore is staged or committed with .mcp.json still tracked, the check FAILS (`untrack_verdict`).

Pure on this repository: reads files plus read-only git (`rev-parse`, `ls-files`, `cat-file`). The self-checks prove
the coherence check is load-bearing and walk the .mcp.json verdict on a throwaway git repository in a temp dir.
Exit 0 = all gates pass; 1 = a packaging invariant is violated.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / ".claude-plugin"
MANIFEST = PLUGIN_DIR / "plugin.json"           # the ONE correct location
ROOT_MANIFEST = ROOT / "plugin.json"            # must NOT exist (silently-ignored shadow)
MARKETPLACE = PLUGIN_DIR / "marketplace.json"
CHANGELOG = ROOT / "CHANGELOG.md"
MCP_NAME = ".mcp.json"                         # must NOT ship at the plugin root (setup writes each project's own)
GITIGNORE = ROOT / ".gitignore"
COMMANDS = ROOT / "commands"
SKILL = ROOT / "skills" / "deep-map" / "SKILL.md"
VERSION_CMD = COMMANDS / "version.md"          # carries the loaded-version canary (the 4th version source)

PLUGIN_NAME = "deep-map"
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
# A released CHANGELOG heading: "## [0.5.2] — 2026-06-29" (NOT "## [Unreleased]").
CHANGELOG_RELEASED = re.compile(r"^##\s*\[(\d+\.\d+\.\d+)\]")
# The /deep-map:version command bakes a LOADED-version literal into its markdown (Claude Code loads
# plugin markdown ONCE per session, so that literal IS the running version). It is a 4th place the
# version lives, so it must stay coherent with plugin.json or the "which version is live?" check lies.
LOADED_CANARY = re.compile(r"<!--\s*deepmap:loaded-version\s*-->")
LOADED_VERSION = re.compile(r"\bv(\d+\.\d+\.\d+)\b")

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


def _load_json(p: Path):
    """Return (obj, error_str). obj is None on any failure."""
    try:
        return json.loads(p.read_text(encoding="utf-8")), ""
    except FileNotFoundError:
        return None, "missing"
    except Exception as exc:  # noqa: BLE001 — any parse/IO failure is a gate failure
        return None, str(exc)


def _marketplace_plugin(mk: dict, name: str):
    """Find the named plugin entry in a marketplace.json `plugins` list."""
    for entry in (mk.get("plugins") or []):
        if isinstance(entry, dict) and entry.get("name") == name:
            return entry
    return None


def _latest_released_changelog_version(text: str):
    for line in text.splitlines():
        m = CHANGELOG_RELEASED.match(line.strip())
        if m:
            return m.group(1)
    return None


def _loaded_version_canary(text: str):
    """Pull the vX.Y.Z from the line carrying the `<!-- deepmap:loaded-version -->` marker in
    commands/version.md. Line-scoped (splitlines + search, no multiline `^…$` anchoring) so CRLF is a
    non-issue — same pattern as the CHANGELOG scanner above. Returns None if the marker is absent."""
    for line in text.splitlines():
        if LOADED_CANARY.search(line):
            m = LOADED_VERSION.search(line)
            return m.group(1) if m else None
    return None


def _bad_root_keys(obj: dict) -> list:
    """Root keys the official `claude plugin validate` would reject as unrecognized — we forbid the
    `_`-prefixed 'comment' keys (e.g. `_note`) that broke marketplace validation. (Self-checked.)"""
    return [k for k in obj if str(k).startswith("_")]


def _gitignore_lists(text: str, name: str) -> bool:
    """Does a .gitignore text carry a line that ignores `name` at the repository root (`name` or `/name`)?
    (Self-checked.)"""
    return any(raw.strip() in (name, "/" + name) for raw in str(text or "").splitlines())


def _git_at_top(root: Path):
    """The git executable when `root` is the TOP of a git checkout, else None (no git, not a checkout, or a directory
    inside one, e.g. a staged copy of the tree inside another repository). Read-only (`git rev-parse`); never
    raises."""
    git = shutil.which("git")
    if not git:
        return None
    try:
        top = subprocess.run([git, "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True,
                             text=True, timeout=30)
        got = top.stdout.strip()
        if top.returncode != 0 or not got:
            return None
        if os.path.normcase(str(Path(got).resolve())) != os.path.normcase(str(Path(root).resolve())):
            return None
        return git
    except Exception:  # noqa: BLE001 — "can't tell" is never a pass or a fail by itself
        return None


def git_tracks(root: Path, rel: str):
    """True / False: does git track `rel` in the git checkout whose TOP is `root`? None when that can't be told: no
    git, not a git checkout, or `root` is not the top of one (e.g. a staged copy of the tree inside another
    directory). Read-only (`git rev-parse` + `git ls-files`); never raises."""
    git = _git_at_top(Path(root))
    if not git:
        return None
    try:
        ls = subprocess.run([git, "-C", str(root), "ls-files", "-z", "--", rel], capture_output=True, timeout=30)
        if ls.returncode != 0:
            return None
        return bool(ls.stdout.strip(b"\0").strip())
    except Exception:  # noqa: BLE001 — "can't tell" is never a pass or a fail by itself
        return None


def git_index_text(root: Path, rel: str):
    """The text of `rel` as git's INDEX holds it (what the next commit takes), or None when that can't be told: no
    git, `root` is not the top of a git checkout, or the index has no `rel`. Read-only (`git cat-file blob :rel`);
    never raises."""
    git = _git_at_top(Path(root))
    if not git:
        return None
    try:
        r = subprocess.run([git, "-C", str(root), "cat-file", "blob", ":" + rel], capture_output=True, timeout=30)
        if r.returncode != 0:
            return None
        return r.stdout.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return None


def index_gitignore_lists(root: Path = ROOT):
    """Does the .gitignore in git's INDEX list the root .mcp.json? None when that can't be told (see
    git_index_text)."""
    text = git_index_text(Path(root), ".gitignore")
    return None if text is None else _gitignore_lists(text, MCP_NAME)


def untrack_verdict(listed, tracked, index_lists) -> str:
    """The "git does not track the root .mcp.json" half, as one word:
      'pass'    git does not track it;
      'skip'    can't tell (no git, or this tree is not the top of a git checkout);
      'pending' git tracks it, the working tree's .gitignore lists it, and the .gitignore in git's INDEX does not:
                the line that stops shipping it is an unstaged edit, and the commit that takes the line must also
                run `git rm --cached .mcp.json`;
      'fail'    git tracks it in any other state: the index's own .gitignore lists it (staged or committed without
                the `git rm --cached`), the index's .gitignore can't be read, or the working tree's does not list it.
    Pure; self-checked (a table and a real git repository)."""
    if tracked is None:
        return "skip"
    if tracked is False:
        return "pass"
    if listed is True and index_lists is False:
        return "pending"
    return "fail"


def mcp_json_untrack_verdict(root: Path = ROOT) -> str:
    """untrack_verdict for the tree at `root` (read-only git)."""
    listed, tracked = shipped_mcp_json_status(root)
    return untrack_verdict(listed, tracked, index_gitignore_lists(root) if tracked else None)


def shipped_mcp_json_status(root: Path = ROOT):
    """(listed_in_gitignore, tracked) for the plugin-root .mcp.json the plugin must NOT ship: listed must be True,
    tracked False (None = not a git checkout's top: skipped). Shared by tests/mcp_transport_gates.py and
    tests/port_registry_gates.py."""
    try:
        text = (Path(root) / ".gitignore").read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        text = ""
    return _gitignore_lists(text, MCP_NAME), git_tracks(Path(root), MCP_NAME)


GIT_RM_HINT = "run: git rm --cached .mcp.json"


def _versions_coherent(*versions) -> bool:
    """True only when every provided (non-None) version string is identical AND at least one was
    provided. A single drift or a missing source ⇒ False. (Load-bearing — self-checked below.)"""
    vs = [v for v in versions]
    if not vs or any(v is None for v in vs):
        return False
    return len(set(vs)) == 1


# --------------------------------------------------------------------------- #
def gate_location() -> None:
    print("\n-- manifest location (root plugin.json is silently ignored) --")
    check("manifest at .claude-plugin/plugin.json exists", MANIFEST.exists(),
          "per the official plugin docs, the manifest MUST live under .claude-plugin/")
    check("NO root-level plugin.json (a silently-ignored shadow)", not ROOT_MANIFEST.exists(),
          "a repo-root plugin.json is NOT discovered by plugin install — move it into .claude-plugin/")


def gate_manifest_shape() -> dict:
    print("\n-- plugin.json shape --")
    obj, err = _load_json(MANIFEST)
    check("plugin.json is valid JSON", obj is not None, err)
    if not isinstance(obj, dict):
        return {}
    for k in ("name", "version", "description"):
        check(f"plugin.json has '{k}'", bool(obj.get(k)))
    check(f"plugin.json name == '{PLUGIN_NAME}'", obj.get("name") == PLUGIN_NAME,
          f"got {obj.get('name')!r}")
    ver = obj.get("version", "")
    check("plugin.json version is semver X.Y.Z", bool(SEMVER.match(str(ver))), f"got {ver!r}")
    # `claude plugin validate` rejects unrecognized root keys — a `_note`-style comment key fails
    # official validation (it was a real submission blocker). Forbid `_`-prefixed root keys.
    bad = _bad_root_keys(obj)
    check("plugin.json has NO unrecognized '_'-prefixed root keys", not bad, f"found {bad}")
    # The manifest schema + the docs example want author-as-object ({name: ...}), not a bare string.
    if "author" in obj:
        check("plugin.json 'author' is an object with a name",
              isinstance(obj.get("author"), dict) and bool(obj["author"].get("name")),
              f"got {obj.get('author')!r}")
    return obj


def gate_marketplace() -> dict:
    print("\n-- marketplace.json shape --")
    obj, err = _load_json(MARKETPLACE)
    check("marketplace.json is valid JSON", obj is not None, err)
    if not isinstance(obj, dict):
        return {}
    check("marketplace.json has a 'plugins' list", isinstance(obj.get("plugins"), list))
    bad = _bad_root_keys(obj)
    check("marketplace.json has NO unrecognized '_'-prefixed root keys", not bad, f"found {bad}")
    check("marketplace.json has a root 'description'", bool(obj.get("description")),
          "claude plugin validate warns when a marketplace has no description")
    entry = _marketplace_plugin(obj, PLUGIN_NAME)
    check(f"marketplace lists the '{PLUGIN_NAME}' plugin", entry is not None)
    if entry:
        check("marketplace deep-map entry has a 'source'", bool(entry.get("source")),
              "needs source (e.g. './') so install can locate the manifest")
        check("marketplace deep-map entry has a 'version'", bool(entry.get("version")))
    return entry or {}


def gate_version_coherence(manifest: dict, mk_entry: dict) -> None:
    print("\n-- ONE coherent version (T16 / FD-4) --")
    mver = manifest.get("version")
    kver = mk_entry.get("version")
    cver = None
    if CHANGELOG.exists():
        cver = _latest_released_changelog_version(CHANGELOG.read_text(encoding="utf-8"))
    lver = None
    if VERSION_CMD.exists():
        lver = _loaded_version_canary(VERSION_CMD.read_text(encoding="utf-8"))
    check("CHANGELOG has a released version heading", cver is not None)
    check("commands/version.md has a loaded-version canary", lver is not None,
          "expected a line like '**DeepMap vX.Y.Z** <!-- deepmap:loaded-version -->'")
    print(f"     plugin.json={mver!r}  marketplace={kver!r}  CHANGELOG-latest={cver!r}  version.md-canary={lver!r}")
    check("plugin.json.version == marketplace deep-map.version", _versions_coherent(mver, kver),
          f"{mver!r} != {kver!r}")
    check("plugin.json.version == latest released CHANGELOG version", _versions_coherent(mver, cver),
          f"{mver!r} != {cver!r}")
    check("plugin.json.version == version.md loaded-version canary", _versions_coherent(mver, lver),
          f"{mver!r} != {lver!r}")
    check("ALL FOUR versions coherent (manifest == marketplace == CHANGELOG == version.md canary)",
          _versions_coherent(mver, kver, cver, lver), f"{mver!r} / {kver!r} / {cver!r} / {lver!r}")


def gate_components() -> None:
    print("\n-- components exist at the plugin ROOT (not nested in .claude-plugin/) --")
    mds = sorted(COMMANDS.glob("*.md")) if COMMANDS.exists() else []
    check("commands/ has >= 4 slash commands", len(mds) >= 4, f"got {len(mds)}")
    check("skills/deep-map/SKILL.md exists", SKILL.exists())


def gate_no_shipped_mcp_json() -> None:
    """The plugin must not ship a root .mcp.json (a plugin's .mcp.json is loaded in every project; the one this
    repository shipped named the FIRST project's ports). setup's write_mcp_json is the only wiring."""
    print("\n-- the plugin ships NO root .mcp.json (setup writes each project's own) --")
    listed, _tracked = shipped_mcp_json_status(ROOT)
    check(".gitignore lists .mcp.json (a checkout's own wiring never reaches the plugin)", listed,
          "add a '.mcp.json' line to .gitignore")
    verdict = mcp_json_untrack_verdict(ROOT)
    if verdict == "skip":
        print("  [skip] git does not track .mcp.json: not checkable here (no git, or this tree is not the top of a git "
              "checkout)")
    elif verdict == "pending":
        print("  [PENDING] git still tracks .mcp.json, and the .gitignore line that stops shipping it is not staged "
              f"yet: the commit that takes that line must also {GIT_RM_HINT} (staged or committed with .mcp.json "
              "still tracked, this check FAILS)")
    else:
        check("git does not track a root .mcp.json (the plugin would ship it)", verdict == "pass", GIT_RM_HINT)


def gate_untrack_verdict_real_git() -> None:
    """Walk untrack_verdict on a REAL throwaway git repository (a stubbed git would hide what the real one answers):
    a committed .mcp.json reads FAIL; the .gitignore line as an unstaged edit reads PENDING; the same line staged, or
    committed, without `git rm --cached` reads FAIL; after `git rm --cached .mcp.json` it reads PASS and the
    working-tree file stays. Only the temp directory this gate creates is written, and it is removed afterwards."""
    print("\n-- self-check: the .mcp.json verdict on a real git repository --")
    git = shutil.which("git")
    if not git:
        print("  [skip] no git binary: the real-git walk of the .mcp.json verdict is not checkable here")
        return
    td = tempfile.mkdtemp(prefix="dm_manifest_git_")
    try:
        repo = Path(td) / "repo"
        repo.mkdir()
        base = [git, "-c", "core.autocrlf=false", "-c", "user.name=gate", "-c", "user.email=gate@example.invalid",
                "-c", "commit.gpgsign=false", "-c", "core.hooksPath=" + str(Path(td) / "no-hooks"), "-C", str(repo)]

        def g(*args) -> bool:
            try:
                return subprocess.run(base + list(args), capture_output=True, timeout=60).returncode == 0
            except Exception:  # noqa: BLE001
                return False

        if not g("init", "-q"):
            print("  [skip] git init failed in a temp dir: the real-git walk is not checkable here")
            return
        (repo / ".mcp.json").write_bytes(b"{}\n")
        (repo / ".gitignore").write_bytes(b"build/\n")
        check("real git: the throwaway repository commits a root .mcp.json",
              g("add", "-A") and g("commit", "-q", "-m", "one") and git_tracks(repo, MCP_NAME) is True)
        check("real git: tracked, and .gitignore does not list it -> fail", mcp_json_untrack_verdict(repo) == "fail")
        (repo / ".gitignore").write_bytes(b"build/\n.mcp.json\n")
        check("real git: tracked, the .gitignore line an unstaged edit -> pending",
              mcp_json_untrack_verdict(repo) == "pending")
        check("real git: the index's .gitignore does not list it yet", index_gitignore_lists(repo) is False)
        g("add", ".gitignore")
        check("real git: .gitignore staged with .mcp.json still tracked -> fail",
              index_gitignore_lists(repo) is True and mcp_json_untrack_verdict(repo) == "fail")
        g("commit", "-q", "-m", "two")
        check("real git: committed without git rm --cached -> fail", mcp_json_untrack_verdict(repo) == "fail")
        g("rm", "-q", "--cached", MCP_NAME)
        check("real git: after git rm --cached .mcp.json -> pass, and the working-tree file stays",
              mcp_json_untrack_verdict(repo) == "pass" and (repo / MCP_NAME).is_file())
    finally:
        _remove_own_tree(td)
    check("real git: the gate's temp repository is removed", not Path(td).exists(), td)


def _remove_own_tree(path: str) -> None:
    """Remove the temp directory this gate created (only that exact path). git writes its object files read-only,
    and on Windows a read-only file can't be deleted until its read-only bit is cleared."""
    import stat

    def _clear_and_retry(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
            func(p)
        except Exception:  # noqa: BLE001 — the existence check after this reports a leftover
            pass

    hook = {"onexc": _clear_and_retry} if sys.version_info >= (3, 12) else {"onerror": _clear_and_retry}
    try:
        shutil.rmtree(path, **hook)    # `onerror` is deprecated from 3.12
    except Exception:  # noqa: BLE001
        pass


def gate_selfcheck_mutation() -> None:
    """Prove the coherence check is load-bearing (not a tautology): it must REJECT a drift and a
    missing source, and ACCEPT a genuine match."""
    print("\n-- self-check: _versions_coherent is load-bearing --")
    check("rejects a version drift", _versions_coherent("0.5.2", "0.5.1") is False)
    check("rejects a missing source (None)", _versions_coherent("0.5.2", None) is False)
    check("rejects all-missing", _versions_coherent() is False)
    check("accepts a genuine 3-way match", _versions_coherent("0.5.2", "0.5.2", "0.5.2") is True)
    check("_bad_root_keys flags a '_note' comment key", _bad_root_keys({"name": "x", "_note": "y"}) == ["_note"])
    check("_bad_root_keys passes a clean manifest", _bad_root_keys({"name": "x", "version": "1"}) == [])
    check("canary extractor pulls vX.Y.Z from the marked line",
          _loaded_version_canary("**DeepMap v1.2.3** <!-- deepmap:loaded-version -->") == "1.2.3")
    check("canary extractor returns None when the marker is absent",
          _loaded_version_canary("**DeepMap v1.2.3** (no marker)") is None)
    check("_gitignore_lists finds '.mcp.json' and '/.mcp.json' lines (CRLF too)",
          _gitignore_lists("x\n.mcp.json\n", ".mcp.json") and _gitignore_lists("a\r\n/.mcp.json\r\n", ".mcp.json"))
    check("_gitignore_lists rejects a comment, a negation and another file's line",
          not _gitignore_lists("# .mcp.json\n!.mcp.json\nsub/.mcp.json\n.mcp.json.bak\n", ".mcp.json"))
    check("git_tracks answers None (can't tell) for a directory that is not a checkout's top",
          git_tracks(COMMANDS, "version.md") is None)
    check("git_index_text answers None (can't tell) for a directory that is not a checkout's top",
          git_index_text(COMMANDS, "version.md") is None)
    check("untrack_verdict: untracked -> pass; can't tell -> skip",
          untrack_verdict(True, False, None) == "pass" and untrack_verdict(True, None, None) == "skip")
    check("untrack_verdict: tracked, listed in the working tree only -> pending",
          untrack_verdict(True, True, False) == "pending")
    check("untrack_verdict: tracked and listed in the index (staged or committed) -> fail",
          untrack_verdict(True, True, True) == "fail")
    check("untrack_verdict: tracked, index .gitignore unreadable -> fail (never pending on 'can't tell')",
          untrack_verdict(True, True, None) == "fail")
    check("untrack_verdict: tracked, the working tree's .gitignore does not list it -> fail",
          untrack_verdict(False, True, False) == "fail" and untrack_verdict(False, True, True) == "fail")


def gate_command_portability() -> None:
    """Slash commands must invoke bundled scripts via ${CLAUDE_PLUGIN_ROOT}, not a bare CWD-relative
    `python build/...`. A bare path only resolves inside the source repo — when the plugin is INSTALLED
    in another project the command's Bash runs in the user's cwd and `build/` is not found. (2026-07-05
    packaging finding; deep-init/oss-kit use the same ${CLAUDE_PLUGIN_ROOT} convention.)"""
    import re as _re
    print("\n-- commands are plugin-root-portable (installable in ANY project) --")
    mds = sorted(COMMANDS.glob("*.md")) if COMMANDS.exists() else []
    # load-bearing self-check: a bare path MUST be detected by the pattern below.
    check("portability check is load-bearing",
          _re.search(r"python\s+build/(provision|deepmap_cli)\.py", "python build/provision.py setup .")
          is not None)
    for md in mds:
        text = md.read_text(encoding="utf-8")
        if not _re.search(r"python\s+\S*build/(provision|deepmap_cli)\.py", text):
            continue  # command doesn't invoke a bundled build/ script
        check(f"{md.name}: no bare CWD-relative 'python build/...'",
              _re.search(r"python\s+build/(provision|deepmap_cli)\.py", text) is None,
              "bare build/ path breaks when the plugin is installed elsewhere")
        check(f"{md.name}: bundled script uses ${{CLAUDE_PLUGIN_ROOT}}",
              "${CLAUDE_PLUGIN_ROOT}/build/" in text,
              'expected python "${CLAUDE_PLUGIN_ROOT}/build/..."')


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — plugin packaging + version-coherence gates (no Neo4j)")
    print("=" * 64)
    gate_location()
    manifest = gate_manifest_shape()
    mk_entry = gate_marketplace()
    gate_version_coherence(manifest, mk_entry)
    gate_components()
    gate_no_shipped_mcp_json()
    gate_command_portability()
    gate_selfcheck_mutation()
    gate_untrack_verdict_real_git()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
