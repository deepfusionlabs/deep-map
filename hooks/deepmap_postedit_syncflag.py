#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap post-edit sync-flag hook (Track D) — a PostToolUse reminder that the graph may be stale.

The DeepMap graph reflects the code as of the LAST sync. After an edit, deepmap_search/deepmap_impact
can silently answer from pre-edit code mid-refactor. This hook fires deterministically AFTER a code
edit (Edit/Write/MultiEdit/NotebookEdit) and injects a SHORT note that the graph may be stale, with
the fix — run /deep-map:refresh. It adds "a commit also re-syncs it" ONLY when this repo's post-commit
hook provably re-syncs THIS project: /deep-map:setup never installs one (only `provision.py git-hook
--install` and the shell installers do), so an unconditional claim told the agent a sync would happen
that never did. ROUND-8 (J1, hook side): the `# deepmap` marker alone is no proof — a hook file other
projects share (a global core.hooksPath, a linked worktree's common hooks dir) can carry ANOTHER
project's block, a Python/Node hook never runs an appended sh block, a block after an unconditional
`exec`/`exit` is unreachable, and an old block can name a deleted plugin's deepmap_cli.py. The claim is
made only when a DeepMap line syncs this project's own root (see _git_hook_installed). ROUND-9 K6: a
double-quoted path is read the way sh reads it (_sh_dq), so the hook install.ps1 writes — `sync "C:\\Src\\proj"`,
backslashes kept by sh — is recognised as this project's block. ROUND-10 L5: nor when the line's Python can never
start — a one-word `'py -3'` (a command-line DEEPMAP_PYTHON an earlier build wrote as ONE sh word), a removed
interpreter, a bare command not on PATH (_interp_runs; provision's git-hook --status calls such a block stale).

Discipline (mirrors hooks/deepmap_access_hook.py — a hook must never harm the session):
  * MARKER-GATED — does NOTHING unless the workspace has a built graph (.deepmap/graph_build.json).
  * DEBOUNCED — fires AT MOST ONCE PER SESSION by default (keyed on the payload's session_id); it does
    NOT emit after every edit. Set DEEPMAP_SYNC_FLAG_EVERY=N to re-remind every N edits instead
    (N=1: after every edit).
  * OPT-OUT — DEEPMAP_SYNC_FLAG=0 (or the master DEEPMAP_HOOKS=0) silences it.
  * SILENT UNDER A POLICY REFUSAL (ROUND-5 G2) — when the last SessionStart heal refused this project's
    database because it serves ANOTHER project's graph (deepmap_hook_state.heal_problem kind "refused"), it
    says nothing: the SessionStart notify already told the agent not to use DeepMap here, and "run
    /deep-map:refresh" would steer toward that graph. The same holds for setup's MCP BLOCKER (ROUND-7 I4:
    kind "blocked") and for a .mcp.json whose MCP ports the registry gives to ANOTHER project. Any other
    recorded failure keeps the reminder.
  * ADDS CONTEXT, NEVER BLOCKS — emits a PostToolUse `additionalContext` envelope with NO permission
    decision. On ANY error it prints nothing and exits 0.
  * AUTO-SYNC — with DeepMap's background refresh on for this project (deepmap_hook_state.autosync_on), only an edit
    of a SOURCE file inside the project counts (an extension DeepMap's census knows); it is recorded in
    .deepmap/last_source_edit.json (one atomic record) and the note says the graph catches up by itself at the next
    session start (AUTO_SYNC_TEXT; the commit clause under the same proof as GIT_HOOK_SUFFIX) — only when that start
    will run it (deepmap_hook_state.autosync_at_next_start: auto_sync.precheck passes — not paused, backing off, too
    slow, recipe-conflicted, or ending with a recorded skip the next run would repeat (needs-timing, recipe:route, …)
    — and, with DEEPMAP_HEAL=0, the database is up); otherwise today's SYNC_TEXT. Any other edit says and records
    nothing. Auto-sync off: exactly today's note, on any edit.

Registered via hooks/hooks.json (PostToolUse, matcher "Edit|Write|MultiEdit|NotebookEdit"). Composes
with the opt-in RM-1 access hook: Claude Code runs all matching hooks and merges their additionalContext.
"""
from __future__ import annotations

import sys

# ROUND-11 R3 — the FIRST thing this hook does when hooks.json runs it. The command is
#   python -X utf8 "<this file>" ; python3 -X utf8 "<this file>" ; exit 0
# (the one form that parses under sh, bash, dash, Windows PowerShell 5.1 and PowerShell 7, and still starts on a
# python3-only host), so where both names exist a second interpreter starts after the first. They share the shell's
# stdin: the first to get here takes the WHOLE payload; the second finds it drained and leaves at once — before any
# import or side effect — so the work is done exactly once. (A `python` that can't run this file never gets here and
# leaves the payload to python3.) Claude Code always sends a payload, so an empty stdin is never a real event. The
# payload is handed back as sys.stdin, where the hook reads it.
if __name__ == "__main__":
    try:
        _PAYLOAD = sys.stdin.buffer.read() if sys.stdin is not None else b""
    except Exception:
        _PAYLOAD = b""
    if not _PAYLOAD.strip():
        raise SystemExit(0)
    import io as _io
    sys.stdin = _io.TextIOWrapper(_io.BytesIO(_PAYLOAD), encoding="utf-8")

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
try:
    import deepmap_hook_state as _hs  # type: ignore
except Exception:  # pragma: no cover
    _hs = None

STATE_FILENAME = "postedit_sync_state.json"
ENV_OPTOUT = "DEEPMAP_SYNC_FLAG"
ENV_EVERY = "DEEPMAP_SYNC_FLAG_EVERY"
SYNC_TEXT = (
    "Source files changed since DeepMap's last graph build — deepmap_search/deepmap_impact reflect "
    "the code as of the last sync and may be stale for what you just edited. Run /deep-map:refresh to "
    "rebuild."
)
GIT_HOOK_SUFFIX = " (A commit also re-syncs it, via this repo's DeepMap git hook.)"
_GIT_HOOK_MARKER = "# deepmap"   # the marker provision.py git-hook --install writes (the installers call it)
# Auto-sync (DeepMap's background refresh at session start, on for this project): the note fires only for a SOURCE
# file inside the project (an extension DeepMap's census counts — deepmap_hook_state.source_exts), says the graph
# catches up by itself (only when the next session start will refresh it — autosync_at_next_start — else SYNC_TEXT),
# and every such edit is recorded (.deepmap/last_source_edit.json) so the next session's
# SessionStart notify knows the graph may lag. A non-source or outside-the-project edit says and records nothing.
AUTO_SYNC_TEXT = (
    "Source files changed since DeepMap's last graph build ({file}) — deepmap_search/deepmap_impact do not include "
    "these edits yet. DeepMap refreshes the graph by itself in the background when a session starts (at most once "
    "every 15 minutes for the same commit){commit}; run "
    "/deep-map:refresh (in the background) only if you need search/impact to reflect these edits in this session."
)
AUTO_COMMIT_CLAUSE = " and after each commit, through this repo's DeepMap git hook"


def _every() -> int:
    """DEEPMAP_SYNC_FLAG_EVERY as a positive int, else 0 (meaning: once per session). Never raises."""
    try:
        n = int(str(os.environ.get(ENV_EVERY, "")).strip())
        return n if n > 0 else 0
    except Exception:
        return 0


def _should_fire(count: int) -> bool:
    if count < 1:
        return False  # state unreadable -> stay quiet
    every = _every()
    if every > 0:
        return (count - 1) % every == 0  # 1st, N+1th, 2N+1th, ... (N=1: every edit)
    return count == 1  # default: once per session


# ROUND-8 J1 (hook side): what a DeepMap post-commit line looks like — provision's block
#   ( PYTHONUTF8=1 nohup '<py>' '<plugin>/build/deepmap_cli.py' sync '<root>' >'<log>' 2>&1 & ) || true   # deepmap
# and the shell installers' double-quoted form. Group 1-3: the CLI path; 4-6: the sync target (single-quoted with
# the '\'' escape, double-quoted, or bare).
_SYNC_RE = re.compile(
    r"""(?:'([^']*deepmap_cli\.py)'|"([^"]*deepmap_cli\.py)"|([^\s'";&|()]*deepmap_cli\.py))\s+sync\s+"""
    r"""(?:'((?:[^']|'\\'')*)'|"((?:[^"\\]|\\.)*)"|([^\s;&|()<>]+))""")
_SH_NAMES = {"sh", "bash", "dash", "zsh", "ksh", "ash", "mksh", "busybox"}
_TOP_TOKENS = (".", "./", "$(git rev-parse --show-toplevel)", "`git rev-parse --show-toplevel`")


def _git_argv(git, root, *args) -> list:
    """git argv for a probe of `root` — deepmap_hook_state.git_argv (core.longpaths on Windows, ROUND-8 J6)."""
    try:
        return _hs.git_argv(git, root, *args)
    except Exception:
        return [git, *(["-c", "core.longpaths=true"] if os.name == "nt" else []), "-C", str(root), *args]


def _sh_path(p):
    """A path as written in a hook (git's sh) -> a Path on this OS: '/c/Users/x' (the Git-Bash form the shell
    installer writes) -> 'C:/Users/x' on Windows. None when empty. Never raises."""
    try:
        s = str(p or "").strip()
        if not s:
            return None
        if os.name == "nt":
            m = re.match(r"^/([A-Za-z])(/.*)?$", s)
            if m:
                s = f"{m.group(1).upper()}:{m.group(2) or '/'}"
        return Path(s)
    except Exception:
        return None


def _same_dir(a, b) -> bool:
    """Do two paths name the same directory (resolved; case-insensitive on Windows)? Never raises."""
    try:
        na = str(Path(a).resolve()).replace("\\", "/").rstrip("/")
        nb = str(Path(b).resolve()).replace("\\", "/").rstrip("/")
        return na.lower() == nb.lower() if os.name == "nt" else na == nb
    except Exception:
        return False


def _runs_as_sh(text: str) -> bool:
    """Would git run this hook's sh code? A shebang naming a POSIX shell (directly or via env), or none
    (git for Windows runs a shebang-less hook through its sh). A Python / Node / other hook never runs an
    appended sh block — chaining one onto it even broke it. Never raises."""
    try:
        first = (text.splitlines() or [""])[0].strip()
        if not first.startswith("#!"):
            return True
        words = first[2:].split()
        if not words:
            return True
        name = words[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
        if name == "env":
            rest = [w for w in words[1:] if not w.startswith("-") and "=" not in w]
            name = rest[0].replace("\\", "/").rsplit("/", 1)[-1].lower() if rest else ""
        return name in _SH_NAMES
    except Exception:
        return False


_REDIR_RE = re.compile(r"^(\d*)(>>|>\||<>|>&|<&|&>>|&>|>|<)(.*)$")


def _exec_replaces_shell(rest: str) -> bool:
    """For `exec <rest>`: True when it runs a command (the shell is REPLACED — nothing after it runs), False when
    it only redirects the shell's own file descriptors (`exec >log 2>&1`, `exec 1>&2`, `exec > "$f"`) or is bare.
    Never raises (-> True: an exec we can't read is treated as terminating, the side that claims less)."""
    try:
        words = rest.split("#", 1)[0].split()
        i = 0
        while i < len(words):
            m = _REDIR_RE.match(words[i])
            if not m:
                return True          # a command word
            if not m.group(3):
                i += 1               # `>` / `2>` alone: the next word is its target
            i += 1
        return False
    except Exception:
        return True


def _terminates(line: str) -> bool:
    """True iff an UNINDENTED hook line ends the script unconditionally, so nothing after it runs: `exec ...`,
    `exit [n]`, or sourcing husky's runner (`. "$(dirname "$0")/h"` / `. "${0%/*}/h"` — it exits itself).
    Indented lines are left alone (they sit inside an if/case/function). Never raises."""
    try:
        if not line or line[0] in " \t":
            return False
        s = line.strip()
        if s.startswith("#"):
            return False
        if re.match(r"^exit\b(?!\s*=)", s):
            return True
        m = re.match(r"^exec\b(?!\s*=)(.*)$", s)
        if m:
            return _exec_replaces_shell(m.group(1))
        return bool(re.match(r"""^\.\s+["']?\$\(dirname\s+"?\$0"?\)/h["']?\s*$""", s)
                    or re.match(r"""^\.\s+["']?\$\{0%/\*\}/h["']?\s*$""", s))
    except Exception:
        return False


def _sh_dq(text: str) -> str:
    """The value sh gives a DOUBLE-QUOTED word: inside "..." a backslash escapes only $ ` " \\ and a newline — before
    any other character it stays (ROUND-9 K6: install.ps1 writes `sync "C:\\Src\\proj"`, which sh runs verbatim;
    unescaping every backslash turned it into `C:Srcproj` and the true claim was never made). Never raises."""
    try:
        return re.sub(r'\\([$`"\\\n])', r"\1", str(text))
    except Exception:
        return str(text)


_LAST_WORD_RE = re.compile(r"""('(?:[^']|'\\'')*'|"(?:[^"\\]|\\.)*"|[^\s'"]+)\s*$""")


def _interp_runs(pre: str) -> bool:
    """ROUND-10 L5 (hook side): can the interpreter word just before a DeepMap line's deepmap_cli.py (`pre` = the line
    up to the CLI path) run at all? False only when it provably can't: a rooted path that does not exist, is a
    directory or (POSIX) is not executable; ONE sh word holding white space that names no file — a command line such as
    `'py -3'` written as a single quoted word, which sh looks up as a command literally named that (provision's
    `git-hook --status` calls such a block stale); a bare command name that is not on PATH. A word the shell computes
    ($VAR, `...`), an option or a relative path can't be judged here -> True. Never raises (-> True)."""
    try:
        m = _LAST_WORD_RE.search(str(pre or "").rstrip())
        if not m:
            return True
        raw = m.group(1)
        if "$" in raw or "`" in raw:
            return True
        if raw.startswith("'"):
            val = raw[1:-1].replace("'\\''", "'")
        elif raw.startswith('"'):
            val = _sh_dq(raw[1:-1])
        else:
            val = raw
        if not val or val.startswith("-") or val in ("(", "nohup", "exec", "env", "command"):
            return True
        p = _sh_path(val)
        if p is not None and p.is_absolute():
            if not p.exists() or p.is_dir():
                return False
            return os.name == "nt" or os.access(str(p), os.X_OK)
        if re.search(r"\s", val):
            return False
        if "/" in val or "\\" in val:
            return True
        return shutil.which(val) is not None
    except Exception:
        return True


def _line_syncs(line: str, root, top) -> bool:
    """True iff one hook line runs an EXISTING deepmap_cli.py `sync <target>` whose target is `root`, with an
    interpreter that can run (_interp_runs — ROUND-10 L5). `.` and `$(git rev-parse --show-toplevel)` mean the work
    tree's top (`top`); any other target the shell computes at commit time is not provably this project. Double-quoted
    paths are read with sh's rules (_sh_dq), so the backslash Windows paths install.ps1 writes match. Never raises."""
    try:
        for m in _SYNC_RE.finditer(line):
            cli = m.group(1) or (_sh_dq(m.group(2)) if m.group(2) is not None else "") or m.group(3) or ""
            if "$" not in cli:
                cp = _sh_path(cli)
                if cp is None or (cp.is_absolute() and not cp.is_file()):
                    continue   # a deleted plugin's CLI: this line syncs nothing
            if not _interp_runs(line[:m.start()]):
                continue       # its Python can never start (ROUND-10 L5): this line syncs nothing
            if m.group(4) is not None:
                target = m.group(4).replace("'\\''", "'")
            elif m.group(5) is not None:
                target = _sh_dq(m.group(5))
            else:
                target = m.group(6) or ""
            if target in _TOP_TOKENS:
                target = top
            elif "$" in target or "`" in target:
                continue
            tp = _sh_path(target)
            if tp is not None and tp.is_absolute() and _same_dir(tp, root):
                return True
    except Exception:
        return False
    return False


def _git_hook_installed(root) -> bool:
    """True iff this repo's post-commit hook provably RE-SYNCS THIS PROJECT on a commit — the only case the
    "a commit also re-syncs it" claim may be made. All must hold:
      * the hook file git itself resolves (`rev-parse --git-path`: core.hooksPath, worktrees) exists, and on
        POSIX is executable (git skips a non-executable hook);
      * git runs it as sh (_runs_as_sh — never a Python/Node hook with sh appended);
      * a line carrying DeepMap's `# deepmap` marker (or inside its sentinel block) runs an existing
        `deepmap_cli.py sync <target>` whose target is THIS project's root — the directory the hooks treat
        as the project (project_root: the sync root, not necessarily the git toplevel) — _line_syncs. A hook
        file other projects share (a global core.hooksPath, a linked worktree's common hooks dir) carrying
        ANOTHER project's block is no proof for this one;
      * no unindented `exec` / `exit` / husky runner precedes that line (_terminates).
    Called only when the flag fires. Never raises (-> False)."""
    try:
        git = shutil.which("git")
        if not git:
            return False
        p = subprocess.run(_git_argv(git, root, "rev-parse", "--show-toplevel", "--git-path", "hooks/post-commit"),
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5)
        lines = [ln.strip() for ln in (p.stdout or "").splitlines() if ln.strip()]
        if p.returncode != 0 or len(lines) < 2:
            return False
        top, rel = lines[0], lines[-1]
        path = Path(rel) if Path(rel).is_absolute() else Path(root) / rel
        if not path.is_file():
            return False
        if os.name != "nt" and not os.access(str(path), os.X_OK):
            return False
        text = path.read_text(encoding="utf-8", errors="replace")
        if _GIT_HOOK_MARKER not in text or not _runs_as_sh(text):
            return False
        in_block = False
        for ln in text.splitlines():
            s = ln.strip()
            if s.startswith("# >>> deepmap"):
                in_block = True
                continue
            if s.startswith("# <<< deepmap"):
                in_block = False
                continue
            if in_block or s.endswith(_GIT_HOOK_MARKER):
                if _line_syncs(ln, root, top):
                    return True
            elif _terminates(ln):
                return False   # everything after this line — DeepMap's block included — is unreachable
        return False
    except Exception:
        return False


def _edited_source(root, payload):
    """The edited file as a '/'-separated path relative to `root` when the payload's tool_input.file_path (or
    notebook_path) lies inside the project and its lowercased suffix is a source extension DeepMap counts — else
    None (no path, outside the project, inside .deepmap, not source). A relative path is read against the payload's
    cwd. Raises LookupError when the source extensions can't be loaded (the caller then keeps today's behaviour).
    Never raises otherwise."""
    exts = _hs.source_exts()
    if not exts:
        raise LookupError("source extensions unavailable")
    try:
        ti = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
        raw = ti.get("file_path") or ti.get("notebook_path")
        if not isinstance(raw, str) or not raw.strip():
            return None
        p = Path(raw)
        if not p.is_absolute():
            p = Path(str(payload.get("cwd") or root)) / p
        p = Path(os.path.abspath(str(p)))
        base = Path(os.path.abspath(str(root)))
        try:
            p, base = p.resolve(), base.resolve()
        except Exception:
            pass
        np, nb = os.path.normcase(str(p)), os.path.normcase(str(base))
        try:
            if os.path.commonpath([np, nb]) != nb or np == nb:
                return None
        except ValueError:
            return None   # another drive
        rel = os.path.relpath(str(p), str(base)).replace("\\", "/")
        if rel.split("/", 1)[0].lower() == ".deepmap":
            return None
        return rel if p.suffix.lower() in exts else None
    except Exception:
        return None


def _auto_main(root, payload) -> int:
    """The edit note while DeepMap's background refresh is on for this project (AUTO_SYNC_TEXT; see above). Raises
    LookupError when it can't tell source from non-source (main then keeps today's behaviour)."""
    rel = _edited_source(root, payload)
    if rel is None:
        return 0   # not a source file of this project: no note, no record, no count
    _hs.record_source_edit(root, rel, payload.get("session_id"))
    count = _hs.bump_session_count(root, STATE_FILENAME, payload.get("session_id"))
    if _should_fire(count):
        if _hs.mcp_json_foreign_owner(root):
            return 0   # the session's deepmap tools are ANOTHER project's servers
        hook = _git_hook_installed(root)
        if _hs.autosync_at_next_start(root, payload):
            _hs.emit("PostToolUse", AUTO_SYNC_TEXT.format(file=rel, commit=AUTO_COMMIT_CLAUSE if hook else ""))
        else:
            # the next session start will NOT refresh it (paused, backing off, too slow, recipe-conflicted, a recorded
            # skip that run would repeat, or DEEPMAP_HEAL=0 with the database down): no promise — today's note
            _hs.emit("PostToolUse", SYNC_TEXT + (GIT_HOOK_SUFFIX if hook else ""))
    return 0


def main() -> int:
    # Read + parse stdin defensively (as UTF-8, whatever the locale); ANY failure is a clean no-op.
    if _hs is None:
        return 0
    raw = _hs.read_stdin_text()
    try:
        payload = json.loads(raw) if raw and raw.strip() else None
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    try:
        root = _hs.resolve_root(payload)
        if root is None or not _hs.graph_present(root):
            return 0
        if not _hs.feature_enabled(ENV_OPTOUT):
            return 0
        # ROUND-5 G2: the last heal REFUSED this project's database on policy (it serves ANOTHER project's
        # graph). "deepmap_search reflects the last sync — run /deep-map:refresh" would steer toward that
        # graph (a refresh refuses there anyway), so stay silent until /deep-map:setup re-checks it. A file
        # read, no probe: a policy refusal is never superseded by one.
        # ROUND-7 I4: likewise under setup's MCP BLOCKER (a DeepMap server setup could not restart holds this
        # project's port and may answer from another graph) — final until setup succeeds.
        problem = _hs.heal_problem(root, recheck=False)
        if problem is not None and problem[0] in ("refused", "blocked"):
            return 0
        if _hs.autosync_on(root, payload):
            try:
                return _auto_main(root, payload)
            except LookupError:
                pass   # source extensions unknown: today's note, on any edit
        count = _hs.bump_session_count(root, STATE_FILENAME, payload.get("session_id"))
        if _should_fire(count):
            if _hs.mcp_json_foreign_owner(root):
                return 0   # the session's deepmap tools are ANOTHER project's servers: a refresh here can't
                #            change what they answer (the SessionStart notify already said so)
            _hs.emit("PostToolUse", SYNC_TEXT + (GIT_HOOK_SUFFIX if _git_hook_installed(root) else ""))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
