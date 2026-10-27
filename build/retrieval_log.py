#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap RM-1 — retrieval logging (opt-in, local-only, zero-egress).

This is the *foundation* for learned per-arm fusion weights (RM-1 / gate G4,
docs/RM1-ENABLE.md). It OBSERVES retrieval; it does NOT
change ranking. `build/retrieval.py` stays frozen — nothing here is wired into the
live search path, and the learned weights this feeds are NOT applied to ranking
until G3 closes and the learned-vs-equal A/B passes (G4).

What it does, and its hard invariants:
  * OPT-IN, off by default. Logging happens only when explicitly enabled
    (env DEEPMAP_RETRIEVAL_LOG=1, or an instance.json flag). `log_retrieval` is a
    no-op otherwise.
  * ZERO-EGRESS. The only artifact is <root>/.deepmap/retrieval_log.jsonl. It never
    leaves the machine; the log dir is gitignored (it matches sync's
    graph_build.json sidecar convention). append_record refuses to write anywhere
    but under <root>/.deepmap/.
  * NEVER raises into the caller. A logging failure must never break a search, so
    every write/read swallows OSError and returns None/[].

Record shape (one JSON object per line):

  {"type":"search","v":1,"qid":"<hash>","query":"...","project":"...","ts":"<iso>",
   "top_k":15,
   "per_arm_candidate_ids":{"vector":["id",...],"bm25":[...],"graph_weighted":[...]},
   "returned":[{"id":"...","file":"build/x.py","name":"foo","line":42}, ...],
   "accessed_ids":[]}

Because DeepMap's fusion (`_weighted_rrf`) is RANK-based (it converts per-arm scores
to ranks internally), storing each arm's candidate ids *in ranked order* is a
FUSION-LOSSLESS record: the offline tuner replays the exact same fusion under
different weights without needing the raw scores. `capture_search` produces that
record faithfully from a live retriever without modifying `search()`.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

LOG_FILENAME = "retrieval_log.jsonl"
INSTANCE_FILENAME = "instance.json"
# v1 = binary access; v2 (RM-1 extension) adds session_id + query features to search records and a
# `kind` to access events. read_records / fold stay version-agnostic: a v1 record folds identically
# (its missing fields default to today's behavior), so the bump is metadata only — never a break.
RECORD_VERSION = 2
# The three fusion arms; MUST == set(retrieval.DEFAULT_ARM_WEIGHTS) keys — enforced by gate_arm_names_match in tests/retrieval_log_gates.py (drift-lock, RED on divergence).
ARM_NAMES = ("vector", "bm25", "graph_weighted")

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
# The learning-loop MASTER switch (one env / one durable instance flag): logging + the post-sync
# offline re-tune + the drift guard. It NEVER implies DEEPMAP_APPLY_LEARNED — applying learned
# weights to live ranking stays the separate 4-condition gate in build/learned_fusion.py.
LEARNING_ENV = "DEEPMAP_LEARNING"

# ---- graded implicit relevance (RM-1 extension, design §2) ------------------
# The agentic "click" is graded, not binary: opening a returned file (Read) is a WEAK positive; an
# Edit/Write (the agent ACTED on it) or a deepmap_impact traversal is a STRONG positive. Feeds the
# tuner's graded metrics (nDCG). An unknown/legacy access -> weak (v1 back-compat: every prior
# accessed id was effectively a weak positive at 0.5, and nDCG at a single grade == the binary case).
GRADE_WEIGHTS: dict[str, float] = {"read": 0.5, "edit": 1.0, "write": 1.0, "impact": 1.0}
GRADE_DEFAULT: float = 0.5


# ---- paths -----------------------------------------------------------------
def log_path(root) -> Path:
    """The one and only log artifact: <root>/.deepmap/retrieval_log.jsonl."""
    return Path(root) / ".deepmap" / LOG_FILENAME


def _instance_path(root) -> Path:
    return Path(root) / ".deepmap" / INSTANCE_FILENAME


# ---- opt-in ----------------------------------------------------------------
def learning_enabled(root=None, *, env=None) -> bool:
    """The learning-loop MASTER switch. Truthy DEEPMAP_LEARNING -> ON; explicit falsy -> OFF
    (vetoes the durable instance flag); unset -> the instance flag
    {"learned_fusion": {"learning_enabled": true}}. It turns on logging + the post-sync offline
    re-tune + the drift guard — and NOTHING live: DEEPMAP_APPLY_LEARNED stays a separate,
    never-implied gate. Never raises."""
    try:
        e = env if env is not None else os.environ
        v = str(e.get(LEARNING_ENV, "")).strip().lower()
        if v in _TRUE:
            return True
        if v in _FALSE:
            return False
        if root is None:
            return False
        data = json.loads(_instance_path(root).read_text(encoding="utf-8"))
        return bool(data.get("learned_fusion", {}).get("learning_enabled", False))
    except Exception:
        return False


def logging_enabled(root=None) -> bool:
    """OFF by default. ON iff DEEPMAP_RETRIEVAL_LOG is truthy, OR (root given) the instance file
    carries {"learned_fusion": {"logging_enabled": true}}, OR the learning-loop master switch is
    on (learning_enabled — env DEEPMAP_LEARNING / instance learning_enabled). An explicit falsy
    DEEPMAP_RETRIEVAL_LOG vetoes ALL of those (most-specific wins). Never raises."""
    env = str(os.environ.get("DEEPMAP_RETRIEVAL_LOG", "")).strip().lower()
    if env in _TRUE:
        return True
    if env in _FALSE:
        return False
    if root is not None:
        try:
            data = json.loads(_instance_path(root).read_text(encoding="utf-8"))
            if bool(data.get("learned_fusion", {}).get("logging_enabled", False)):
                return True
        except Exception:
            pass
    return learning_enabled(root)


# ---- record construction ----------------------------------------------------
def _qid(query: str, ts: str, returned_ids) -> str:
    h = hashlib.sha1()
    h.update((query or "").encode("utf-8", "replace"))
    h.update(b"\x00")
    h.update((ts or "").encode("utf-8", "replace"))
    h.update(b"\x00")
    h.update("\x00".join(str(i) for i in (returned_ids or [])).encode("utf-8", "replace"))
    return h.hexdigest()[:12]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _redact_query(query) -> str:
    """When DEEPMAP_LOG_REDACT is truthy, STORE a stable hash instead of the raw query text (privacy —
    the log is local, but a proprietary query string may still be sensitive). Off by default -> the
    raw query is stored. NOTE: `qid` is always computed from the RAW query, so folding + determinism
    are identical whether or not redaction is on. Never raises."""
    try:
        if str(os.environ.get("DEEPMAP_LOG_REDACT", "")).strip().lower() in _TRUE:
            return "redacted:" + hashlib.sha1((query or "").encode("utf-8", "replace")).hexdigest()[:16]
    except Exception:
        pass
    return query if isinstance(query, str) else ("" if query is None else str(query))


# ---- query features -> intent bucket (shared by log-time + tune-time) -------
# Cheap, interpretable features of the query, computed at LOG time (from the live query text, never
# re-derived from a truncated log) so the offline tuner can learn DIFFERENT arm blends for different
# question kinds. Deliberately tiny + robust — never raises; malformed input -> the 'default' bucket.
def _looks_like_symbol(t) -> bool:
    """True if a token looks like a code identifier: contains '_' or '.', or is camelCase/PascalCase."""
    if not isinstance(t, str) or len(t) < 2:
        return False
    if "_" in t or "." in t:
        return True
    return any(t[i].isupper() and t[i - 1].islower() for i in range(1, len(t)))


# Cues that a query is asking about the call graph (DeepMap's measured edge: deepmap_impact).
_IMPACT_CUES = ("who calls", "callers", " calls ", "call site", "impact", "blast radius",
                "depend", "affected", "reverse", "breaks if", "used by", "references to")


def query_features(query, returned=None) -> dict:
    """Cheap, log-time features of a query. Never raises. Keys:
      len_bucket ('short'<=2 / 'medium'3-6 / 'long'>6 tokens), has_symbol_token (a code identifier),
      is_impact_query (call-graph cue words), fts_heavy (a short symbol-ish query BM25 answers best)."""
    try:
        q = query if isinstance(query, str) else ""
        ql = q.lower()
        tokens = q.split()
        n = len(tokens)
        len_bucket = "short" if n <= 2 else ("medium" if n <= 6 else "long")
        has_symbol = any(_looks_like_symbol(t) for t in tokens)
        is_impact = any(cue in ql for cue in _IMPACT_CUES)
        fts_heavy = has_symbol and n <= 3
        return {"len_bucket": len_bucket, "has_symbol_token": bool(has_symbol),
                "is_impact_query": bool(is_impact), "fts_heavy": bool(fts_heavy)}
    except Exception:
        return {"len_bucket": "medium", "has_symbol_token": False,
                "is_impact_query": False, "fts_heavy": False}


def intent_bucket(features) -> str:
    """Map query features to one of 4 interpretable intent buckets. Malformed -> 'default'.
    'impact' (call-graph questions), 'symbol_short' (a short identifier lookup), 'nl_long' (a long
    natural-language query), 'default' (everything else / cold-start fallback)."""
    if not isinstance(features, dict):
        return "default"
    if features.get("is_impact_query"):
        return "impact"
    if features.get("has_symbol_token") and features.get("len_bucket") == "short":
        return "symbol_short"
    if features.get("len_bucket") == "long":
        return "nl_long"
    return "default"


def _normalize_returned(returned) -> list[dict]:
    """Accept either raw search-result dicts or already-shaped entries; emit the minimal
    {id,file,name,line} shape the access hook matches against."""
    out = []
    for e in returned or []:
        out.append({
            "id": e.get("id"),
            "file": (e.get("file") or "").replace("\\", "/"),
            "name": e.get("name"),
            "line": e.get("line"),
        })
    return out


def build_record(query: str, per_arm_candidate_ids: dict, returned, *,
                 project: str | None = None, top_k: int | None = None,
                 ts: str | None = None, session_id: str | None = None) -> dict:
    """Build one search record (schema v2). `ts`/`session_id` are injectable for determinism (tests).
    `features` are computed from the live query text so the offline tuner can bucket by intent. Extra
    fields over v1 are additive: a v1 reader ignores them, a v2 reader defaults them when absent."""
    ts = ts or _now_iso()
    per_arm = {arm: list(per_arm_candidate_ids.get(arm, [])) for arm in ARM_NAMES}
    ret = _normalize_returned(returned)
    query_stored = _redact_query(query)
    return {
        "type": "search",
        "v": RECORD_VERSION,
        "qid": _qid(query, ts, [e["id"] for e in ret]),
        "query": query_stored,
        "project": project,
        "ts": ts,
        "top_k": top_k,
        "session_id": session_id,
        "features": query_features(query, ret),
        "per_arm_candidate_ids": per_arm,
        "returned": ret,
        "accessed_ids": [],
    }


# ---- append / read (never raise, zero-egress) ------------------------------
def append_record(root, record: dict):
    """Append one JSON line to <root>/.deepmap/retrieval_log.jsonl. Returns the path, or
    None on any failure (a bad root, an unwritable dir). NEVER raises. Zero-egress: the
    only write target is under <root>/.deepmap/."""
    try:
        lp = log_path(root)
    except Exception:
        return None
    try:
        # Confinement guard: refuse to write anywhere but under <root>/.deepmap/.
        confine = (Path(root) / ".deepmap").resolve()
        if not str(lp.resolve().parent).startswith(str(confine)):
            return None
        lp.parent.mkdir(parents=True, exist_ok=True)
        with lp.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return lp
    except (OSError, ValueError, TypeError):
        return None


def log_retrieval(root, query: str, per_arm_candidate_ids: dict, returned, *,
                  project: str | None = None, top_k: int | None = None,
                  ts: str | None = None, session_id: str | None = None):
    """Opt-in convenience: build + append a search record IFF logging is enabled for `root`.
    Returns the log path, or None (disabled / failed). Never raises."""
    if not logging_enabled(root):
        return None
    rec = build_record(query, per_arm_candidate_ids, returned,
                       project=project, top_k=top_k, ts=ts, session_id=session_id)
    return append_record(root, rec)


DEFAULT_MAX_LOG_BYTES = 5_000_000   # ~5 MB cap for the local retrieval log (rotation drops oldest)


def rotate_log(root, max_bytes: int = DEFAULT_MAX_LOG_BYTES) -> bool:
    """Cap <root>/.deepmap/retrieval_log.jsonl at ~max_bytes by dropping the OLDEST lines, preserving
    JSONL validity (only whole, parseable lines are kept — never a torn half-line). No-op if under the
    cap or absent. Zero-egress (writes only under .deepmap/, same confinement guard as append_record).
    Never raises. Returns True iff it rewrote the file."""
    try:
        lp = log_path(root)
    except Exception:
        return False
    try:
        if not lp.exists() or lp.stat().st_size <= max_bytes:
            return False
        confine = (Path(root) / ".deepmap").resolve()
        if not str(lp.resolve().parent).startswith(str(confine)):
            return False
        lines = [ln for ln in lp.read_text(encoding="utf-8").splitlines() if ln.strip()]
        kept: list[str] = []
        size = 0
        for ln in reversed(lines):          # keep newest, drop oldest, whole valid lines only
            try:
                json.loads(ln)
            except (ValueError, TypeError):
                continue
            b = len(ln.encode("utf-8", "replace")) + 1
            if size + b > max_bytes and kept:
                break
            kept.append(ln)
            size += b
        kept.reverse()
        lp.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8")
        return True
    except (OSError, ValueError, TypeError):
        return False


def read_records(root) -> list[dict]:
    """Parse the JSONL log, skipping blank/malformed lines. [] if absent. Never raises."""
    try:
        text = log_path(root).read_text(encoding="utf-8")
    except (OSError, ValueError, TypeError):   # TypeError guards log_path(None)
        return []
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


# ---- faithful capture from a live retriever (does NOT modify search()) -----
def _rank_ids(arm_results) -> list[str]:
    """Ids of one arm's results in DESCENDING score order, de-duped keeping the best score —
    exactly how _weighted_rrf ranks within an arm. Ordered ids are all the tuner needs."""
    best: dict = {}
    for r in arm_results or []:
        rid = r.get("id")
        s = r.get("score", 0.0)
        if rid not in best or s > best[rid]:
            best[rid] = s
    return [rid for rid, _ in sorted(best.items(), key=lambda kv: kv[1], reverse=True)]


def _live_candidate_floor() -> int:
    """retrieval._candidate_floor() — the opt-in DEEPMAP_CANDIDATE_FLOOR search() applies — read from
    retrieval itself so the log, the offline replays and search() share ONE rule. Imported lazily: the
    PostToolUse access hook imports this module and must not pay for loading retrieval.py. 0 (search()'s
    own default) on any failure. Never raises."""
    try:
        _b = str(Path(__file__).resolve().parent)
        if _b not in sys.path:
            sys.path.insert(0, _b)
        from retrieval import _candidate_floor
        return int(_candidate_floor())
    except Exception:
        return 0


def fetch_k_for(top_k: int) -> int:
    """The per-arm candidate depth live search() fetches for `top_k`: max(top_k*3, _candidate_floor())
    (retrieval.py — DEEPMAP_CANDIDATE_FLOOR unset -> top_k*3, today's default)."""
    return max(top_k * 3, _live_candidate_floor())


def per_arm_candidates(retriever, query: str, top_k: int = 15) -> dict:
    """The per-arm ranked candidate ids for `query`, recomputed by re-invoking the SAME arm
    methods search() uses (same qvec, same fetch_k — fetch_k_for(top_k), which honours the opt-in
    candidate floor exactly like search()). Because fusion is rank-based, these ordered id lists are a
    fusion-lossless mirror of what search() fused. Does NOT modify search()."""
    qvec = retriever._embed_or_none(query)
    fetch_k = fetch_k_for(top_k)
    vec = retriever._arm_vector(qvec, fetch_k) if qvec is not None else []
    fts = retriever._arm_fulltext(query, fetch_k)
    graph = retriever._arm_graph_weighted(qvec, fetch_k) if qvec is not None else []
    return {
        "vector": _rank_ids(vec),
        "bm25": _rank_ids(fts),
        "graph_weighted": _rank_ids(graph),
    }


def capture_search(retriever, query: str, top_k: int = 15):
    """Run the retriever and return (results, per_arm_candidate_ids).

    `results` come straight from the FROZEN `retriever.search()` — so returned ids can never
    drift from live ranking. `per_arm_candidate_ids` are recomputed by re-invoking the same
    arm methods search() uses, at the same per-arm depth (fetch_k_for — the candidate floor
    included); because fusion is rank-based, the ordered id lists are a fusion-lossless mirror
    of what search() fused. This is the record the opt-in logging path (and the offline tuner)
    consume."""
    results = retriever.search(query, top_k)
    return results, per_arm_candidates(retriever, query, top_k)


_MAX_WALK = 64            # ancestor levels walked looking for the git work tree's top (bounded)


def _home_state_dir():
    """DEEPMAP_HOME's own resolved `.deepmap`-style state dir (machine state, never a project marker)."""
    try:
        return Path(os.environ.get("DEEPMAP_HOME", str(Path.home() / ".deepmap"))).resolve()
    except Exception:
        return None


def project_root(start=None):
    """The PROJECT root the RM-1/enrich artifacts (<root>/.deepmap/...) belong to, for a directory
    that may be a SUBDIRECTORY of it — so `learning`/`insights`/`tune`/`enrich` and search logging run
    from anywhere in a project read and write ONE state: the root `sync` itself syncs
    (sync._sync_root) and the hooks read (hooks/deepmap_hook_state.project_root).

    Resolution (stat-only — no subprocess on the MCP hot path) — ONE rule shared with sync._sync_root,
    the hooks (deepmap_hook_state.project_root) and provision's MCP-server root:
      * inside a git work tree (an ancestor holds a `.git` dir or file): the OUTERMOST ancestor of
        `start` (inclusive) up to the work tree's top that holds a sync manifest
        `.deepmap/graph_build.json` — a stale manifest an old subdirectory sync left behind never
        shadows the root's; with no manifest at all, `start` itself — the directory a sync of it
        would write its .deepmap/ state into (it used to be the git toplevel here, the one case where
        `learning --enable` / enrich / tune disagreed with the sync and the hooks);
      * outside a git work tree: `start` itself — a non-git directory is its own project (sync syncs it
        as given, and the Neo4j routing keys it on its own path).
    DEEPMAP_HOME's own `.deepmap` (machine state, not a project) is never taken as a marker. NOT cached:
    a walk is a handful of stat() calls (well under a millisecond), and a per-process cache went stale
    the moment a sync wrote the first manifest in a long-lived process. Never raises -> None on
    failure."""
    try:
        s = (Path(start) if start is not None else Path.cwd()).resolve()
    except Exception:
        return None
    home_state = _home_state_dir()
    best = top = None
    try:
        d = s
        for _ in range(_MAX_WALK):
            try:
                marker = d / ".deepmap"
                if (marker / "graph_build.json").is_file() and (home_state is None
                                                                 or marker.resolve() != home_state):
                    best = d                     # keep walking: the OUTERMOST manifest wins
                if (d / ".git").exists():
                    top = d                      # the work tree's top bounds the walk
                    break
            except OSError:
                pass
            if d.parent == d:
                break
            d = d.parent
    except Exception:
        best = top = None
    return (best or s) if top is not None else s


def cli_command(sub: str) -> str:
    """A copy-pasteable command for this plugin's CLI — the interpreter (DEEPMAP_PYTHON, else the one
    running now) + the ABSOLUTE build/deepmap_cli.py path + `sub`. Hints must be runnable as written
    from the user's project, where no `deepmap` executable or relative `build/` path exists. Never
    raises."""
    try:
        interp = (os.environ.get("DEEPMAP_PYTHON", "").strip() or sys.executable or "python")
        cli = (Path(__file__).resolve().parent / "deepmap_cli.py").as_posix()
        return f'"{interp.replace(chr(92), "/")}" "{cli}" {sub}'.rstrip()
    except Exception:
        return f"deepmap_cli.py {sub}".rstrip()


def default_root():
    """The project root for logging: DEEPMAP_PROJECT_ROOT if set (verbatim — provision injects the MCP
    servers' SYNC root there, computed by this same rule), else project_root() of the process cwd (the
    outermost synced ancestor up to the git toplevel — so a search run from a subdirectory of a synced
    project logs to the project's own log). None on failure. Used by the opt-in live call site so it
    needs no explicit root argument."""
    env = os.environ.get("DEEPMAP_PROJECT_ROOT")
    if env:
        return Path(env)
    try:
        return project_root(Path.cwd())
    except Exception:
        return None


def maybe_log_search(retriever, query: str, results, *, root=None, top_k: int | None = None,
                     project: str | None = None, session_id: str | None = None):
    """The single OPT-IN live call site. When logging is DISABLED (the default) this is a cheap
    no-op — one `logging_enabled()` check, no arm re-run, zero overhead on the hot path. When
    ENABLED it records the search (per-arm candidates + the returned results) to the local log.
    NEVER raises — a logging failure must never break a live search. Live ranking is untouched:
    `results` are the caller's already-computed search() output; this only OBSERVES."""
    try:
        if root is None:
            root = default_root()
        if root is None or not logging_enabled(root):
            return None
        per_arm = per_arm_candidates(retriever, query, top_k or 15)
        if project is None:
            try:
                project = Path(root).name
            except Exception:
                project = None
        if session_id is None:
            session_id = os.environ.get("DEEPMAP_SESSION_ID") or None
        return log_retrieval(root, query, per_arm, results, project=project, top_k=top_k,
                             session_id=session_id)
    except Exception:
        return None


# ---- access correlation (pure; used by the hook + the tuner) ---------------
def _as_list(x) -> list:
    """A list, or [] for anything else — the untrusted-input coercion for a log field."""
    return x if isinstance(x, list) else []


def _norm_path(p) -> str:
    return (p if isinstance(p, str) else "").replace("\\", "/").strip()


def _paths_match(returned_file: str, accessed_path: str) -> bool:
    """True iff the two paths refer to the same file. Match is BOUNDARY-ANCHORED: full equality, or
    one is a path-*component* suffix of the other (`build/a.py` vs `/repo/build/a.py`). A bare string
    suffix is deliberately NOT enough — `src/xa.py` must not match an access of `a.py`."""
    a = _norm_path(returned_file)
    b = _norm_path(accessed_path)
    if not a or not b:
        return False
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def _returned_hit(rec: dict, target: str, is_path: bool):
    """The id of the first returned entry in `rec` that matches `target` (a file path, boundary-
    anchored, or a symbol name). None if none. Never raises."""
    for e in _as_list(rec.get("returned")):
        if not isinstance(e, dict):
            continue
        if is_path and _paths_match(e.get("file", ""), target):
            return e.get("id")
        if (not is_path) and e.get("name") == target:
            return e.get("id")
    return None


def _returned_rank_ids(rec: dict) -> list:
    """Ids of the record's returned entries in their STORED order — which is search()'s fused rank
    order (the order the tuner / cascade model reason about). Skips malformed entries."""
    out = []
    for e in _as_list(rec.get("returned")):
        if isinstance(e, dict) and e.get("id") is not None:
            out.append(e.get("id"))
    return out


def _skip_above_negatives(returned_ids: list, accessed: set) -> list:
    """Cascade/click-model soft negatives: returned ids ranked STRICTLY ABOVE the highest-ranked
    accessed id that were NOT themselves accessed (the agent scanned past them to reach the one it
    used). [] when the top result was accessed or nothing was accessed. De-duped, rank order."""
    top = None
    for i, rid in enumerate(returned_ids):
        if rid in accessed:
            top = i
            break
    if not top:                      # None (no access) or 0 (top result accessed) -> no skips
        return []
    seen, out = set(), []
    for rid in returned_ids[:top]:
        if rid is not None and rid not in accessed and rid not in seen:
            seen.add(rid)
            out.append(rid)
    return out


def _grade_of(kind, grade_weights: dict) -> float:
    k = kind.strip().lower() if isinstance(kind, str) else ""
    return grade_weights.get(k, GRADE_DEFAULT)


def fold_graded(records: list[dict], *, grade_weights: dict | None = None) -> list[dict]:
    """Fold access events into GRADED soft-relevance qrels + skip-above negatives (design §2, the
    RM-1 extension). Like `fold_access_events` but each returned search copy also carries:
      * "graded":    {id: weight}   — the strongest access grade the id earned (Read 0.5 < Edit/impact 1.0),
      * "negatives": [id, ...]      — skip-above (cascade) soft negatives,
      * "accessed_ids": [...]       — sorted keys of `graded` (binary view, back-compat).
    An access is credited to the MOST-RECENT preceding search that surfaced it (nearest-preceding;
    this handles both many-searches->one-access and one-search->many-accesses since each access is
    credited independently). Pure; never mutates input; never raises on malformed records."""
    gw = grade_weights or GRADE_WEIGHTS
    stream: list[dict] = []          # search copies, in stream order
    graded_maps: list[dict] = []     # parallel {id: weight}
    for r in records or []:
        if not isinstance(r, dict):
            continue
        t = r.get("type")
        if t == "search":
            stream.append(dict(r))
            # Seed with any pre-existing accessed_ids as weak positives (v1 back-compat).
            gm: dict = {}
            for i in _as_list(r.get("accessed_ids")):
                if isinstance(i, (str, int, float)):
                    gm[i] = max(gm.get(i, 0.0), GRADE_DEFAULT)
            graded_maps.append(gm)
        elif t == "access":
            paths = [_norm_path(p) for p in _as_list(r.get("paths")) if isinstance(p, str)]
            symbols = [s for s in _as_list(r.get("symbols")) if isinstance(s, str) and s]
            grade = _grade_of(r.get("kind"), gw)
            for target, is_path in ([(p, True) for p in paths] + [(s, False) for s in symbols]):
                for idx in range(len(stream) - 1, -1, -1):   # newest-first
                    hit = _returned_hit(stream[idx], target, is_path)
                    if hit is not None:
                        graded_maps[idx][hit] = max(graded_maps[idx].get(hit, 0.0), grade)
                        break
    out = []
    for rec, gm in zip(stream, graded_maps):
        rec = dict(rec)
        acc_ids = sorted(gm.keys(), key=lambda x: str(x))   # str() sort tolerates mixed id types
        rec["accessed_ids"] = acc_ids
        rec["graded"] = {k: gm[k] for k in acc_ids}
        rec["negatives"] = _skip_above_negatives(_returned_rank_ids(rec), set(acc_ids))
        out.append(rec)
    return out


def fold_access_events(records: list[dict]) -> list[dict]:
    """Fold access events into search records, populating a BINARY `accessed_ids` (sorted, de-duped).

    An access is credited to the MOST-RECENT preceding search whose `returned` set contains a
    matching file (path) or symbol (name) — the same-turn 'the agent read what search surfaced'
    signal (design §2). Returns copies of the search records; access records are dropped. Pure;
    never mutates input; never raises. (Back-compat wrapper over `fold_graded` — identical
    `accessed_ids`; the graded/negatives fields are omitted here.)"""
    out = []
    for rec in fold_graded(records):
        rec = dict(rec)
        rec.pop("graded", None)
        rec.pop("negatives", None)
        out.append(rec)
    return out
