#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap — the SINGLE source of truth for the search/impact bounds: result counts, query length, time.

The MCP tools enforce a hard clamp on `top_k` / `limit` at the boundary (a frozen contract:
design.md:51). The CLI (`deepmap search|impact`) mirrors the SAME bounds. Before this module the
CLI carried the max values as magic literals (`50` / `100`) duplicated from
`deepmap_search_server.py`; both call sites now import from here so the numbers cannot drift.

v0.28.0 adds the bounds the 2026-09 live validation found missing: a query-length cap (a 5,000-char
query took 13–35 s to embed), per-call and cold-start time limits for the MCP tools, bounded Neo4j
connect/acquire timeouts, and `bounded_tool` — which runs a sync tool body in a worker thread under
those limits so one slow call can no longer block the server's event loop for every other caller.
Round 3 (E4) adds what the servers announce about the project they serve (project_tool_describer /
project_instructions — the deployed stateless-HTTP transport can't see a client's roots, so the tool
descriptions name the project instead) and AuthBackoff (no new Neo4j login for a doubling window after a
rejected one, so a misconfigured server can't keep Neo4j's login lock engaged). The label's inverse lives next to
it (parse_project_instructions / parse_project_label, built from the same sentence constants): provision reads a
live server's label with it to prove which project the server serves (ISS-build:024). Round 9 (K9) measures and
embeds the docstring NORMALISED (inspect.cleandoc) — Python 3.13+ dedents docstrings at compile time and
3.10-3.12 do not, so the same description used to differ by ~70 chars between them — and adds a compact
remedy tier so the project line keeps its "different project?" remedy for realistic project paths.

Side-effect-free on import (constants + helpers; `bounded_tool` imports anyio lazily) — safe to import
from the long-lived MCP server at module load and from the CLI at parser-build time. No Neo4j, no
network, zero egress.
"""
from __future__ import annotations

import os
import sys

TOP_K_MAX = 50            # frozen MCP contract (design.md:51)
TOP_K_DEFAULT = 15
IMPACT_LIMIT_MAX = 100
IMPACT_LIMIT_DEFAULT = 30

QUERY_MAX_CHARS = 2000            # longest query / text the tools embed (~300 tokens, ~2-3 s on one CPU thread)
TOOL_TIMEOUT_S = 60.0             # per-call deadline once warm (env DEEPMAP_MCP_TOOL_TIMEOUT_S)
WARMUP_TIMEOUT_S = 300.0          # cold start: model load + driver connect (100-170 s seen on a loaded box;
                                  # env DEEPMAP_MCP_WARMUP_TIMEOUT_S)
NEO4J_CONNECTION_TIMEOUT_S = 15.0         # TCP connect
NEO4J_ACQUISITION_TIMEOUT_S = 30.0        # a pooled/new connection incl. the Bolt handshake (driver default 60)
MODEL_RETRY_S = 300.0             # after a FAILED model load, answer keyword-only for this long before the
                                  # next load attempt (env DEEPMAP_MCP_MODEL_RETRY_S)
ROOTS_TIMEOUT_S = 3.0             # how long the project guard waits for the client's MCP roots (env
                                  # DEEPMAP_MCP_ROOTS_TIMEOUT_S); no answer -> fail open
AUTH_BACKOFF_S = 10.0             # after Neo4j REJECTS the server's login, answer with that error for this long
                                  # WITHOUT a new login attempt (env DEEPMAP_MCP_AUTH_BACKOFF_S); the window
                                  # doubles per consecutive rejection, up to AUTH_BACKOFF_MAX_S
AUTH_BACKOFF_MAX_S = 120.0        # env DEEPMAP_MCP_AUTH_BACKOFF_MAX_S


def clamp_int(value, lo: int, hi: int, name: str) -> tuple[int, str | None]:
    """Clamp `value` into the inclusive range [lo, hi].

    Returns `(clamped, note)`. `note` is a human-readable message ONLY when the input was out of
    range or not an integer; it is `None` when the value is already a valid in-range integer — so
    an in-range (or default) CLI invocation stays silent and its behaviour is unchanged. Never
    raises: a non-integer coerces to `lo` with an explanatory note.
    """
    try:
        raw = int(value)
    except (TypeError, ValueError):
        return lo, f"--{name} {value!r} is not an integer; using {lo}."
    clamped = min(max(lo, raw), hi)
    if clamped != raw:
        return clamped, f"--{name} {raw} is out of range [{lo}, {hi}]; clamped to {clamped}."
    return clamped, None


def cap_query(text: str, max_chars: int = QUERY_MAX_CHARS) -> tuple[str, str | None]:
    """Cap a search query / embed text at `max_chars`, cutting at the last whitespace so no half word
    is searched. Returns `(text, note)`: a query within the cap comes back byte-identical with note
    None; a longer one comes back truncated with a one-line note for the caller to show."""
    text = text or ""
    if len(text) <= max_chars:
        return text, None
    cut = text[:max_chars]
    if not text[max_chars].isspace():                  # the cap fell inside a word: drop that fragment
        i = max(cut.rfind(c) for c in " \t\r\n")
        if i > 0:
            cut = cut[:i]
    cut = cut.rstrip()
    return cut, (f"query truncated to {len(cut)} of {len(text)} characters (the {max_chars}-character "
                 "cap) — put the key terms first.")


# The CLI's first cut looked the helper up under this name; an alias is cheaper than a silent no-op.
clamp_query = cap_query


def _env_seconds(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def tool_timeout_s() -> float:
    """Per-call deadline for an MCP tool once the server is warm."""
    return _env_seconds("DEEPMAP_MCP_TOOL_TIMEOUT_S", TOOL_TIMEOUT_S)


def warmup_timeout_s() -> float:
    """Deadline for the cold-start work (embedding model + Neo4j driver) a first call may have to wait on."""
    return _env_seconds("DEEPMAP_MCP_WARMUP_TIMEOUT_S", WARMUP_TIMEOUT_S)


def model_retry_s() -> float:
    """After a failed embedding-model load, how long the servers answer without the model (search:
    keyword-only; embed: an error) before trying to load it again."""
    return _env_seconds("DEEPMAP_MCP_MODEL_RETRY_S", MODEL_RETRY_S)


def roots_timeout_s() -> float:
    """How long the search server's project guard waits for the client's MCP roots before failing open."""
    return _env_seconds("DEEPMAP_MCP_ROOTS_TIMEOUT_S", ROOTS_TIMEOUT_S)


def neo4j_driver_timeouts() -> dict:
    """GraphDatabase.driver(...) kwargs that bound connecting to a dead or frozen Neo4j."""
    return {"connection_timeout": NEO4J_CONNECTION_TIMEOUT_S,
            "connection_acquisition_timeout": NEO4J_ACQUISITION_TIMEOUT_S}


# ---- which project a server serves (round 3, E4) ------------------------------------------------------
# The servers run STATELESS streamable-HTTP, so a tool call can't see the client's MCP roots and the project
# guard can't engage there. What an agent CAN always see, at zero per-call cost, is each tool's description
# and the server's `instructions` (both sent at connect time) — so a server started with DEEPMAP_PROJECT_ROOT
# / DEEPMAP_PROJECT_KEY (provision injects them) names its project in both, and an agent working in another
# project can notice it is wired to the wrong index. Without the env nothing changes (descriptions stay the
# docstrings; no instructions).

def _one_line(text: str) -> str:
    """`text` with every control / non-printable character replaced, so an odd path can't break or inject
    lines into a description."""
    return "".join(c if c.isprintable() else "?" for c in str(text))


def project_from_env(env=None) -> tuple[str, str]:
    """(DEEPMAP_PROJECT_ROOT, DEEPMAP_PROJECT_KEY), stripped; '' when unset or blank."""
    env = os.environ if env is None else env
    return (str(env.get("DEEPMAP_PROJECT_ROOT") or "").strip(),
            str(env.get("DEEPMAP_PROJECT_KEY") or "").strip())


def label_path(root) -> str:
    """A project path exactly as project_label puts it between the parentheses: single-line (control characters
    neutralised) and stripped. A consumer that compares a parsed label with its own paths normalises them with this
    first (ISS-build:024). Never raises."""
    try:
        return _one_line(root or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _label_name(path: str) -> str:
    """The <name> of a '<name> (<path>)' label: the path's last component — the path itself for a filesystem / drive
    root, which has no basename."""
    from pathlib import PurePath
    return PurePath(path).name or path


_KEY_LABEL_LEAD = "with DeepMap key "
_SPLIT_TRIES = 64   # ' (' positions parse_project_label tries — a server's text is untrusted input; a real path has few


def project_label(root: str = "", key: str = "") -> str | None:
    """How a server names its project: '<name> (<path>)' from the root, 'with DeepMap key <key>' from the
    key alone, None when neither is known. Single-line (control characters neutralised). Never raises.
    parse_project_label is its inverse."""
    try:
        root, key = label_path(root), _one_line(key or "").strip()
        if root:
            return f"{_label_name(root)} ({root})"
        if key:
            return f"{_KEY_LABEL_LEAD}{key}"
    except Exception:  # noqa: BLE001
        pass
    return None


def parse_project_label(label) -> "tuple[str, str] | None":
    """The inverse of project_label: (path, '') for '<name> (<path>)', ('', key) for 'with DeepMap key <key>', None for
    anything else. A path may itself hold ' (' and ')' ('shop (copy)'): the split taken is the one whose <name> is the
    path's own last component, exactly as project_label built it (so another string that merely CONTAINS a path in
    parentheses is not a label). Never raises."""
    try:
        s = str(label or "")
        if s.endswith(")"):
            i, tries = s.find(" ("), 0
            while i != -1 and tries < _SPLIT_TRIES:
                tries += 1
                name = s[:i]
                if s.endswith(name + ")"):   # cheap necessary condition: the path ends with its own <name>
                    path = s[i + 2:-1]
                    if path and path == path.strip() and _label_name(path) == name:
                        return (path, "")
                i = s.find(" (", i + 1)
        if s.startswith(_KEY_LABEL_LEAD):
            key = s[len(_KEY_LABEL_LEAD):]
            if key and key == key.strip() and key.isprintable():
                return ("", key)
    except Exception:  # noqa: BLE001
        pass
    return None


# Claude Code truncates an MCP tool description (and a server's instructions) at 2,048 chars ("… [truncated]",
# Claude Code 2.1.x), so the project line comes FIRST and is kept short enough to leave the docstring's usage
# guidance room to survive whole.
CLIENT_DESCRIPTION_MAX = 2048
_ELSEWHERE = ("Working in a different project? These answers are not about it — run /deep-map:setup there (or "
              "reload Claude Code so its own .mcp.json is used).")
# The same remedy in fewer words, used when the full one would push a description past CLIENT_DESCRIPTION_MAX
# (round 9, K9): a realistic ~80-char project path plus deepmap_impact's docstring no longer loses the remedy.
_ELSEWHERE_SHORT = "In a different project? Run /deep-map:setup there, or reload for its own .mcp.json."


def _clean_doc(doc: str | None) -> str | None:
    """`doc` as Python 3.13+ stores it on every supported Python (inspect.cleandoc: the continuation lines'
    common indentation and the surrounding blank lines removed; idempotent). 3.10-3.12 keep a docstring's source
    indentation, 3.13+ strip it at compile time, so measuring the raw `__doc__` made a description's length —
    and whether the remedy fit — depend on the interpreter (round 9, K9). None stays None."""
    if not doc:
        return doc
    import inspect
    return inspect.cleandoc(doc)


def project_tool_describer(lead: str, *, answers_about_project: bool = True, env=None):
    """A bounded_tool `describe` callable: docstring -> '<lead> of project <label> only. <remedy>' + a blank
    line + the docstring, or None (keep the docstring as the description) when the server has no project env.
    `lead` e.g. "Searches the DeepMap graph"; the embed server passes answers_about_project=False (a query
    vector does not depend on the project). The docstring is normalised first (_clean_doc), so the text is the
    same on every supported Python. If the whole text would pass the client's CLIENT_DESCRIPTION_MAX (a long
    path), the project line uses the compact remedy (_ELSEWHERE_SHORT); if that is still too long it drops the
    remedy sentence (the instructions carry it) and, if still too long, shows the path shortened in the middle
    — so the docstring's guidance is never cut."""
    root, key = project_from_env(env)
    label = project_label(root, key)

    def describe(doc: str | None) -> str | None:
        if label is None:
            return None
        doc = _clean_doc(doc) or ""
        if not answers_about_project:
            return (f"{lead} {label} — the vector itself does not depend on the project; pair it with that "
                    f"project's deepmap_search.\n\n{doc}")
        full = f"{lead} of project {label} only. {_ELSEWHERE}\n\n{doc}"
        if len(full) > CLIENT_DESCRIPTION_MAX:
            full = f"{lead} of project {label} only. {_ELSEWHERE_SHORT}\n\n{doc}"
        if len(full) > CLIENT_DESCRIPTION_MAX:
            full = f"{lead} of project {label} only.\n\n{doc}"
        if len(full) > CLIENT_DESCRIPTION_MAX and root:
            # An extreme path: shorten it in the MIDDLE (its start and its project end stay comparable).
            shown = _one_line(root).strip()
            keep = len(shown) - (len(full) - CLIENT_DESCRIPTION_MAX) - 1
            if keep >= 40:
                head = keep // 3
                shown = shown[:head] + "…" + shown[len(shown) - (keep - head):]
                full = f"{lead} of project {project_label(shown, key)} only.\n\n{doc}"
        return full
    return describe


# The two `instructions` sentences as (text before the label, text after it) — the ONE source for both the
# formatter (project_instructions) and its inverse (parse_project_instructions), which provision's MCP ownership
# check reads a live server's label with (ISS-build:024: it used to re-parse this prose with its own regex).
_INSTRUCTIONS = {
    "search": ("This DeepMap server indexes project ",
               ". deepmap_search and deepmap_impact answer about that project's code only. " + _ELSEWHERE),
    "embed": ("This DeepMap server embeds queries for project ",
              "; it pairs with that project's deepmap_search."),
}
_PARSE_MAX_CHARS = 65536   # an announcement is one path + two short sentences; anything longer is not one
_FALLBACK_SCAN_CHARS = 8192   # how far a later wording (no exact template) is scanned for the label's end


def project_instructions(server: str, env=None) -> str | None:
    """The MCP `instructions` a server announces at initialize (None without the project env, so the
    initialize result is unchanged): which project it serves, and what to do when that is not the agent's.
    parse_project_instructions is its inverse."""
    label = project_label(*project_from_env(env))
    if label is None:
        return None
    lead, tail = _INSTRUCTIONS["search" if server == "search" else "embed"]
    return f"{lead}{label}{tail}"


def _label_at_start(rest: str) -> "tuple[str, str] | None":
    """A label at the START of `rest`, whatever wording follows it: the first '<name> (<path>)' whose <name> is the
    path's last component and whose ')' ends a sentence or the text, else a key-only label up to its sentence end.
    Bounded (_FALLBACK_SCAN_CHARS, _SPLIT_TRIES): a server's text is untrusted input."""
    rest = rest[:_FALLBACK_SCAN_CHARS]
    i, tries = rest.find(" ("), 0
    while i != -1 and tries < _SPLIT_TRIES:
        tries += 1
        name = rest[:i]
        k = rest.find(name + ")", i + 2) if name else -1   # the path ends with its own <name>, then ')'
        while k != -1:
            j = k + len(name)
            if j + 1 == len(rest) or rest[j + 1] in ".;, ":
                got = parse_project_label(rest[:j + 1])
                if got is not None:
                    return got
            k = rest.find(name + ")", k + 1)
        i = rest.find(" (", i + 1)
    if rest.startswith(_KEY_LABEL_LEAD):
        words = rest[len(_KEY_LABEL_LEAD):].split(None, 1)
        key = words[0].rstrip(".;,") if words else ""
        if key:
            return parse_project_label(_KEY_LABEL_LEAD + key)
    return None


def parse_project_instructions(text) -> "tuple[str, str] | None":
    """The inverse of project_instructions: the project a DeepMap server's MCP `instructions` name, as
    parse_project_label gives it — (path, '') or ('', key) — or None when `text` is not such an announcement (None,
    empty, other prose, a '<name> (<path>)' whose name is not the path's basename). The server's own sentences
    (_INSTRUCTIONS) are matched exactly first; a text that only STARTS like one (a later wording of what follows the
    label) is read up to the label's end (_label_at_start). Never raises."""
    try:
        s = str(text or "")
        if not s.strip() or len(s) > _PARSE_MAX_CHARS:
            return None
        for lead, tail in _INSTRUCTIONS.values():
            if s.startswith(lead) and s.endswith(tail) and len(s) > len(lead) + len(tail):
                got = parse_project_label(s[len(lead):len(s) - len(tail)])
                if got is not None:
                    return got
        for lead, _tail in _INSTRUCTIONS.values():
            if s.startswith(lead):
                got = _label_at_start(s[len(lead):])
                if got is not None:
                    return got
    except Exception:  # noqa: BLE001
        pass
    return None


# ---- backing off after a rejected Neo4j login (round 3, E4) ---------------------------------------------
# Neo4j counts failed logins per user and, after dbms.security.auth_max_failed_attempts (default 3), LOCKS
# the user for auth_lock_time; every further failure keeps the lock engaged — locking out sync, the CLI and
# setup's own checks too. A server started with credentials Neo4j rejects therefore must not re-try the login
# on every tool call: AuthBackoff answers with the diagnosis, without contacting Neo4j, for a window that
# doubles per consecutive rejection. An OUTAGE is not a rejection and is never backed off (T10 fail-loud,
# and a restarted Neo4j is picked up on the next call).

_AUTH_CODES = ("Security.Unauthorized", "Security.AuthenticationRateLimit", "Security.CredentialsExpired")


def auth_backoff_s() -> float:
    """The first backoff window after a rejected Neo4j login."""
    return _env_seconds("DEEPMAP_MCP_AUTH_BACKOFF_S", AUTH_BACKOFF_S)


def auth_backoff_max_s() -> float:
    """The longest backoff window (never below the first one)."""
    return max(auth_backoff_s(), _env_seconds("DEEPMAP_MCP_AUTH_BACKOFF_MAX_S", AUTH_BACKOFF_MAX_S))


def _is_rate_limit(exc) -> bool:
    try:
        code = str(getattr(exc, "code", "") or "")
        return "AuthenticationRateLimit" in code or "too many times in a row" in str(exc).lower()
    except Exception:  # noqa: BLE001
        return False


def _auth_error_here(e) -> bool:
    """`e` ITSELF (not what it was raised from) is a rejected Neo4j login. Never raises."""
    try:
        # Only if the driver is ALREADY loaded: an exception can't be a neo4j AuthError otherwise, and importing
        # the driver here (~1-2 s cold) would slow down every non-Neo4j error path (e.g. a tool deadline).
        auth_error = getattr(sys.modules.get("neo4j.exceptions"), "AuthError", None)
        if isinstance(auth_error, type) and isinstance(e, auth_error):
            return True
        code = str(getattr(e, "code", "") or "")
        return type(e).__name__ == "AuthError" or any(c in code for c in _AUTH_CODES) or _is_rate_limit(e)
    except Exception:  # noqa: BLE001
        return False


def _chain(exc, depth: int = 8):
    """`exc` and the exceptions it was raised from (__cause__, else __context__), cycle-safe."""
    seen, e = set(), exc
    while e is not None and id(e) not in seen and len(seen) < depth:
        seen.add(id(e))
        yield e
        e = getattr(e, "__cause__", None) or getattr(e, "__context__", None)


def is_neo4j_auth_error(exc) -> bool:
    """True iff `exc`, or an exception it was raised from, is Neo4j REJECTING a login: AuthError /
    Neo.ClientError.Security.Unauthorized, the temporary lock after repeated failures (AuthenticationRateLimit
    — the driver raises a plain ClientError for it) or CredentialsExpired. An outage, a query error or any
    other exception is False. Never raises."""
    try:
        return any(_auth_error_here(e) for e in _chain(exc))
    except Exception:  # noqa: BLE001
        return False


def _short_neo4j_error(exc) -> str:
    """'<code>: <server message>' of the auth failure in `exc`'s chain, without the driver's gql_status
    dump. Never raises."""
    try:
        e = next((c for c in _chain(exc) if _auth_error_here(c)), exc)
        msg = _one_line(getattr(e, "message", None) or str(e)).strip().rstrip(".")
        code = str(getattr(e, "code", "") or "")
        return (f"{code}: {msg}" if code else f"{type(e).__name__}: {msg}")[:300]
    except Exception:  # noqa: BLE001
        return type(exc).__name__


class Neo4jAuthBackoff(RuntimeError):
    """The tool error while AuthBackoff holds off after a rejected Neo4j login (no Neo4j contact)."""


class AuthBackoff:
    """Hold off new Neo4j logins after a rejection. Thread-safe; `clock` is injectable for tests.

    check()            — raise Neo4jAuthBackoff (the diagnosis) inside the window, else return;
    record(exc)        — for an auth failure: count it (once per exception object), (re)open the window and
                         return the Neo4jAuthBackoff to raise instead; for anything else return None;
    success(since=t)   — a Neo4j call that STARTED at t succeeded: reset, unless a rejection came later."""

    def __init__(self, where: str = "Neo4j", clock=None):
        import threading
        import time
        self.where = where
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self.streak = 0
        self._until = 0.0
        self._failed_at = float("-inf")
        self._why = ""
        self._rate_limited = False

    def now(self) -> float:
        """The backoff's clock (pass a reading taken before a Neo4j call to success(since=...))."""
        return self._clock()

    def remaining(self) -> float:
        with self._lock:
            return max(0.0, self._until - self._clock()) if self.streak else 0.0

    def _message(self, left: float) -> str:
        import math
        if self._rate_limited:
            what = (f"{self.where} is temporarily refusing DeepMap's logins after repeated failed login "
                    f"attempts ({self._why}). To let that login lock expire — every new attempt prolongs it, "
                    "and it locks out sync and the CLI too —")
        else:
            what = (f"{self.where} rejected this DeepMap server's credentials ({self._why}). To avoid tripping "
                    "Neo4j's login lock — which would also lock out sync and the CLI —")
        return (f"{what} the server will not try again for {max(1, math.ceil(left))} s. Run /deep-map:doctor "
                "to see which Neo4j this project uses and how to fix its credentials; the server retries on its "
                "own after that.")

    def check(self) -> None:
        with self._lock:
            left = self._until - self._clock()
            if self.streak and left > 0:
                raise Neo4jAuthBackoff(self._message(left))

    def record(self, exc) -> "Exception | None":
        if isinstance(exc, Neo4jAuthBackoff):
            return exc
        if not is_neo4j_auth_error(exc):
            return None
        with self._lock:
            if not getattr(exc, "_deepmap_auth_counted", False):
                self.streak += 1
                now = self._clock()
                window = min(auth_backoff_s() * (2 ** min(self.streak - 1, 30)), auth_backoff_max_s())
                self._until = now + window
                self._failed_at = now
                self._why = _short_neo4j_error(exc)
                self._rate_limited = any(_is_rate_limit(c) for c in _chain(exc))
                try:
                    exc._deepmap_auth_counted = True
                except Exception:  # noqa: BLE001 — an exception that refuses attributes: may count twice
                    pass
            return Neo4jAuthBackoff(self._message(max(0.0, self._until - self._clock())))

    def success(self, since: float | None = None) -> None:
        with self._lock:
            if not self.streak:
                return
            if since is not None and since <= self._failed_at:
                return
            self.streak, self._until, self._failed_at, self._why = 0, 0.0, float("-inf"), ""
            self._rate_limited = False


# ---- an unreachable Neo4j, in words the agent can act on (round 7) -----------------------------------------
# An auth rejection (AuthBackoff) and a missed deadline already read as a diagnosis with a next step; a stopped
# or hibernated instance still reached the agent as the driver's raw ServiceUnavailable text. It stays a loud
# isError (T10: never an empty answer) — only the words change, and the driver's own detail is kept.
_OUTAGE_TYPES = ("ServiceUnavailable", "SessionExpired")


class Neo4jUnreachable(RuntimeError):
    """The tool error when the project's Neo4j cannot be reached (stopped, hibernated, restarting)."""


def _outage_here(e) -> bool:
    try:
        exc_mod = sys.modules.get("neo4j.exceptions")   # only when the driver is loaded: no import on this path
        for name in _OUTAGE_TYPES:
            cls = getattr(exc_mod, name, None)
            if isinstance(cls, type) and isinstance(e, cls):
                return True
        return type(e).__name__ in _OUTAGE_TYPES
    except Exception:  # noqa: BLE001
        return False


def neo4j_unreachable_message(exc, where: str = "Neo4j") -> "str | None":
    """The readable error for an unreachable Neo4j in `exc`'s chain (ServiceUnavailable / SessionExpired), or
    None when `exc` is anything else — an auth rejection, a deadline, a query error. Never raises."""
    try:
        if is_neo4j_auth_error(exc):
            return None
        e = next((c for c in _chain(exc) if _outage_here(c)), None)
        if e is None:
            return None
        detail = _one_line(" ".join(str(getattr(e, "message", None) or e).split())).rstrip(".")[:300]
        return (f"{where} is not reachable ({type(e).__name__}: {detail}). This project's Neo4j is probably "
                "stopped or hibernated (it does not survive a reboot) — run /deep-map:setup to start it again "
                "(the graph is kept; no re-sync needed), or /deep-map:status to check. No results were computed: "
                "this is not an empty answer.")
    except Exception:  # noqa: BLE001
        return None


async def _in_thread(fn, timeout_s: float, on_timeout: str):
    """Run the blocking `fn` in a worker thread; past `timeout_s` stop waiting (the thread is abandoned
    and finishes on its own) and raise TimeoutError(on_timeout) — so the tool call fails in bounded time."""
    import anyio
    with anyio.move_on_after(timeout_s):
        return await anyio.to_thread.run_sync(fn, abandon_on_cancel=True)
    raise TimeoutError(on_timeout)


def bounded_tool(server, warm=None, soft_warm=None, guard=None, backoff=None, describe=None):
    """Decorator: register a SYNC tool with the FastMCP `server` as an ASYNC tool whose body runs in a
    worker thread, so the event loop keeps serving other callers (FastMCP runs a sync tool directly ON
    the loop). Per call, in order:
      * `guard()` (optional, async, on the loop): returns a problem string to refuse the call with (an
        isError), or None to go on — the search server's project guard;
      * `backoff.check()` (optional, an AuthBackoff): inside the window after a rejected Neo4j login the
        call is refused with that diagnosis WITHOUT contacting Neo4j;
      * `warm()` (optional): cold-start work the body cannot do without (e.g. opening the Neo4j driver),
        under warmup_timeout_s(); its failure or a missed deadline IS the tool error (fail loud);
      * `soft_warm()` (optional): cold-start work the body can do WITHOUT (e.g. loading the embedding
        model — search degrades to keyword-only), under what is left of the same warm-up budget; a
        failure or a missed deadline is logged and the body runs anyway;
      * the body, under tool_timeout_s().
    With `backoff`, a rejected login in warm() or the body opens / extends the window and the call fails
    with the diagnosis; a success resets it. An unreachable Neo4j (ServiceUnavailable / SessionExpired) fails
    with neo4j_unreachable_message — still an isError, now with the next step. A slow first load is never
    mistaken for a hung query, and a missed deadline is a tool error (isError), never an empty result.
    `describe(docstring)` (optional) gives the advertised description (None keeps the docstring); it is handed
    the NORMALISED docstring (_clean_doc), so a description is identical on Python 3.10-3.12 and 3.13+. Returns the
    sync function unchanged: it stays the directly-callable contract, and the advertised parameters come from
    it (functools.wraps)."""
    import functools
    import sys
    import time

    def register(fn):
        async def run(**kwargs):
            budget = warmup_timeout_s()
            started = time.monotonic()
            if warm is not None:
                await _in_thread(warm, budget,
                                 f"DeepMap is still starting up after {budget:g}s (loading the embedding "
                                 "model / connecting to Neo4j) — retry in a moment, or check /deep-map:status.")
            if soft_warm is not None:
                left = budget - (time.monotonic() - started)
                try:
                    if left <= 0:
                        raise TimeoutError(f"no warm-up budget left after {budget:g}s")
                    await _in_thread(soft_warm, left, f"not ready within the {budget:g}s warm-up budget")
                except Exception as exc:  # noqa: BLE001 — optional warm-up: the body degrades without it
                    print(f"[{fn.__name__}] optional warm-up skipped ({type(exc).__name__}: {exc})",
                          file=sys.stderr)
            limit = tool_timeout_s()
            return await _in_thread(functools.partial(fn, **kwargs), limit,
                                    f"{fn.__name__} did not answer within {limit:g}s — this project's Neo4j "
                                    "may be hung or overloaded, so the call was abandoned. Check "
                                    "/deep-map:status, then retry.")

        @functools.wraps(fn)
        async def tool(**kwargs):
            if guard is not None:
                problem = await guard()
                if problem:
                    raise ValueError(problem)
            since = None
            if backoff is not None:
                backoff.check()
                since = backoff.now()
            try:
                result = await run(**kwargs)
            except Exception as exc:
                if backoff is not None:
                    friendly = backoff.record(exc)
                    if friendly is not None and friendly is not exc:
                        raise friendly from exc
                unreachable = neo4j_unreachable_message(exc, getattr(backoff, "where", None) or "Neo4j")
                if unreachable:
                    raise Neo4jUnreachable(unreachable) from exc
                raise
            if backoff is not None:
                backoff.success(since=since)
            return result

        description = describe(_clean_doc(fn.__doc__)) if describe is not None else None
        server.tool(description=description)(tool)
        return fn
    return register
