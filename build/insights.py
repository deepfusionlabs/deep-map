#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap RM-1 — `deepmap insights`: a LOCAL, READ-ONLY summary of the opt-in retrieval log
(zero-egress, stdlib-only, no Neo4j). It READS <root>/.deepmap/retrieval_log.jsonl; it writes
NOTHING — not even under .deepmap/ — and changes no ranking (build/retrieval.py stays frozen).

Honest about sample size by construction: every metric reports its n, and below the tuner's own
cold-start thresholds (150 labeled queries overall / 40 per intent bucket — IMPORTED from
tune_weights, never re-declared) the report prints counts only and refuses conclusions: arm win
RATES are null/absent, and the tuner preview states that equal weights stand. Descriptive counts
(intent mix, zero-result, never-used) always render — they describe the log and conclude nothing.

Metric definitions (over the v2 record schema):
  * arm win — among LABELED queries (tune_weights.build_qrels: >=1 returned result later
    accessed), the winning arm(s) are those whose per-arm candidate list ranked a BEST-GRADED id
    (an Edit/impact outranks a weak Read as ground truth) highest; ties share 1/m fractionally;
    a query no arm surfaced is counted as no_arm_surfaced, never silently dropped.
  * zero-result — searches whose `returned` is empty.
  * never-used ("abandonment") — searches with results but no captured access, WITH an explicit
    honesty guard: when the log carries no access signal at all, the metric is NOT computed
    (the opt-in hook is likely unregistered — 'not captured' must never read as 'abandoned');
    searches from before the first access event are excluded and reported as pre-hook. Always an
    UPPER BOUND: access capture is opt-in and file-level.
  * tuner preview — what tune_profiles(records=...) would produce today; pure replay, nothing
    written (the `written: false` field is hard-coded).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_BUILD = Path(__file__).resolve().parent
if str(_BUILD) not in sys.path:
    sys.path.insert(0, str(_BUILD))
import retrieval_log as _rl      # noqa: E402
import tune_weights as _tw       # noqa: E402
import instance_state as _is     # noqa: E402

INSIGHTS_VERSION = 1
ARM_NAMES = tuple(_rl.ARM_NAMES)
# The tuner's cold-start thresholds — the single source of truth for "enough data to conclude".
DEFAULT_MIN_LABELED = _tw.DEFAULT_MIN_LABELED
DEFAULT_MIN_BUCKET = _tw.DEFAULT_MIN_LABELED_BUCKET

_MAX_EXAMPLES = 5


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _searches(records) -> list:
    return [r for r in (records or []) if isinstance(r, dict) and r.get("type") == "search"]


def _bucket_of(rec) -> str:
    return _rl.intent_bucket(rec.get("features"))


def _display_query(rec):
    """The query text safe to SHOW: a non-empty str that is not a redaction hash, else None."""
    q = rec.get("query")
    if isinstance(q, str) and q and not q.startswith("redacted:"):
        return q
    return None


def filter_since(records, since):
    """(kept, n_undated): records whose str `ts` >= `since` by PLAIN string comparison — the log's
    fixed ISO format makes a date prefix like '2026-07-01' work lexicographically. Records with a
    missing/non-str ts are EXCLUDED from a --since view and counted. Never raises."""
    if not since:
        return list(records or []), 0
    kept, undated = [], 0
    for r in records or []:
        if not isinstance(r, dict):
            continue
        ts = r.get("ts")
        if not isinstance(ts, str):
            undated += 1
            continue
        if ts >= str(since):
            kept.append(r)
    return kept, undated


# ---- metrics ----------------------------------------------------------------
def arm_win_rates(labeled, *, min_labeled: int = DEFAULT_MIN_LABELED,
                  min_bucket: int = DEFAULT_MIN_BUCKET) -> dict:
    """Which arm FIRST surfaced the result the user actually used — overall and per intent
    bucket. Input is tune_weights.build_qrels output (the same labeled population as the tuner,
    so ns match `tune --status`). `rates` are populated ONLY when sufficient; below the
    threshold a consumer sees raw fractional `wins` and a null `rates`. Never raises."""
    def _empty():
        return {"n_labeled": 0, "sufficient": False, "wins": {a: 0.0 for a in ARM_NAMES},
                "no_arm_surfaced": 0, "rates": None}

    overall = _empty()
    by_bucket: dict = {}
    for q in labeled or []:
        try:
            graded = q.get("graded") if isinstance(q.get("graded"), dict) else {}
            if not graded:
                continue
            g_star = max(graded.values())
            targets = {rid for rid, g in graded.items() if g == g_star}
            per_arm = q.get("per_arm") if isinstance(q.get("per_arm"), dict) else {}
            ranks = {}
            for a in ARM_NAMES:
                ids = per_arm.get(a) if isinstance(per_arm.get(a), list) else []
                r = None
                for i, rid in enumerate(ids):
                    if rid in targets:
                        r = i + 1
                        break
                if r is not None:
                    ranks[a] = r
            bucket = q.get("bucket") if isinstance(q.get("bucket"), str) else "default"
            row = by_bucket.setdefault(bucket, _empty())
            overall["n_labeled"] += 1
            row["n_labeled"] += 1
            if not ranks:
                overall["no_arm_surfaced"] += 1
                row["no_arm_surfaced"] += 1
                continue
            best = min(ranks.values())
            winners = [a for a, r in ranks.items() if r == best]
            share = 1.0 / len(winners)
            for a in winners:
                overall["wins"][a] += share
                row["wins"][a] += share
        except Exception:
            continue
    overall["sufficient"] = overall["n_labeled"] >= min_labeled
    if overall["sufficient"] and overall["n_labeled"]:
        overall["rates"] = {a: overall["wins"][a] / overall["n_labeled"] for a in ARM_NAMES}
    for row in by_bucket.values():
        row["sufficient"] = overall["sufficient"] and row["n_labeled"] >= min_bucket
        if row["sufficient"] and row["n_labeled"]:
            row["rates"] = {a: row["wins"][a] / row["n_labeled"] for a in ARM_NAMES}
    return {"overall": overall, "by_bucket": by_bucket}


def zero_result(searches) -> dict:
    """Searches that returned NOTHING — a descriptive 'X of N' count (never threshold-gated)."""
    out = {"n": 0, "n_search": 0, "examples": [], "by_bucket": {}}
    for rec in searches or []:
        if not isinstance(rec, dict):
            continue
        out["n_search"] += 1
        try:
            if _rl._returned_rank_ids(rec):
                continue
            out["n"] += 1
            b = _bucket_of(rec)
            out["by_bucket"][b] = out["by_bucket"].get(b, 0) + 1
            q = _display_query(rec)
            if q is not None:
                out["examples"].append({"query": q, "bucket": b})
        except Exception:
            continue
    out["examples"] = out["examples"][-_MAX_EXAMPLES:][::-1]   # most recent first
    return out


def intent_mix(searches) -> dict:
    """Bucket counts + up to 3 example queries per bucket (redaction-aware)."""
    buckets: dict = {}
    n_search = 0
    for rec in searches or []:
        if not isinstance(rec, dict):
            continue
        n_search += 1
        try:
            b = _bucket_of(rec)
            row = buckets.setdefault(b, {"n": 0, "n_redacted": 0, "examples": []})
            row["n"] += 1
            q = _display_query(rec)
            if q is None:
                if isinstance(rec.get("query"), str) and rec["query"].startswith("redacted:"):
                    row["n_redacted"] += 1
            elif len(row["examples"]) < 3:
                row["examples"].append(q)
        except Exception:
            continue
    return {"n_search": n_search, "buckets": buckets}


def abandonment(records, folded) -> dict:
    """Searches whose results were never used — WITH the honesty guard: no access signal at all
    means the metric is NOT computed ('not captured' is not 'abandoned'), and searches from
    before the first access event are excluded as pre-hook. Always an upper bound."""
    out = {"status": "no_access_data", "n_abandoned": 0, "n_considered": 0, "n_pre_hook": 0,
           "ts_domain": False, "examples": [], "by_bucket": {}}
    try:
        events = [r for r in (records or []) if isinstance(r, dict) and r.get("type") == "access"]
        folded_searches = [r for r in (folded or []) if isinstance(r, dict) and r.get("type") == "search"]
        labeled_any = any(_rl._as_list(r.get("accessed_ids")) for r in folded_searches)
        if not events and not labeled_any:
            return out
        out["status"] = "ok"
        ts_list = [e.get("ts") for e in events if isinstance(e.get("ts"), str)]
        first_ts = min(ts_list) if ts_list else None
        out["ts_domain"] = first_ts is not None
        for rec in folded_searches:
            if not _rl._returned_rank_ids(rec):
                continue
            accessed = _rl._as_list(rec.get("accessed_ids"))
            if accessed:
                out["n_considered"] += 1
                continue
            ts = rec.get("ts")
            post_hook = (first_ts is None) or (isinstance(ts, str) and ts >= first_ts)
            if not post_hook:
                out["n_pre_hook"] += 1
                continue
            out["n_considered"] += 1
            out["n_abandoned"] += 1
            b = _bucket_of(rec)
            out["by_bucket"][b] = out["by_bucket"].get(b, 0) + 1
            q = _display_query(rec)
            if q is not None:
                out["examples"].append({"query": q, "bucket": b})
        out["examples"] = out["examples"][-_MAX_EXAMPLES:][::-1]
        return out
    except Exception:
        return out


def _project_lf(lf) -> dict | None:
    if not isinstance(lf, dict):
        return None
    prof = lf.get("profiles") if isinstance(lf.get("profiles"), dict) else {}
    return {"arm_weights": lf.get("arm_weights"), "applied": lf.get("applied"),
            "beats_equal": lf.get("beats_equal"), "significant": lf.get("significant"),
            "reason": lf.get("reason"), "n_labeled_queries": lf.get("n_labeled_queries"),
            "generated_utc": lf.get("generated_utc"),
            "profiles": {b: {"arm_weights": (r or {}).get("arm_weights"),
                             "n_labeled": (r or {}).get("n_labeled")}
                         for b, r in prof.items() if isinstance(r, dict)}}


def tuner_preview(records, root=None, *, min_labeled: int | None = None,
                  min_bucket: int | None = None) -> dict:
    """What the tuner WOULD produce today — a pure tune_profiles(records=...) replay. Writes
    NOTHING; `written: false` is hard-coded. `current` mirrors the stored learned_fusion block
    (None when absent). Never raises."""
    out = {"current": None, "would_be": None, "written": False}
    try:
        if root is not None:
            cur = _is.read_instance(root).get("learned_fusion")
            # a `learning --enable` block holds no tune -> "none yet", never a dict of Nones
            out["current"] = _project_lf(cur) if _is.has_tune_result(cur) else None
    except Exception:
        pass
    try:
        kw = {}
        if isinstance(min_labeled, int):
            kw["min_labeled"] = min_labeled
        if isinstance(min_bucket, int):
            kw["min_labeled_bucket"] = min_bucket
        res = _tw.tune_profiles(records=list(records or []), **kw)
        out["would_be"] = _project_lf(res)
        out["would_be"]["reason"] = res.get("reason")
    except Exception:
        out["would_be"] = None
    return out


# ---- the aggregator ---------------------------------------------------------
def build_insights(records, *, root=None, since=None, min_labeled: int = DEFAULT_MIN_LABELED,
                   min_bucket: int = DEFAULT_MIN_BUCKET, with_tuner: bool = True) -> dict:
    """The one JSON-safe report dict (v1). Pure over `records`; `root` is used ONLY to read the
    stored learned_fusion block for the tuner preview and for display. Never raises."""
    data = {"v": INSIGHTS_VERSION, "root": str(root) if root is not None else None,
            "generated_utc": _now_iso(),
            "window": {"since": since, "ts_min": None, "ts_max": None, "n_undated": 0, "n_total": 0},
            "counts": {"n_records": 0, "n_search": 0, "n_access_events": 0, "n_labeled": 0,
                       "n_redacted_queries": 0},
            "sufficient": False,
            "thresholds": {"min_labeled": min_labeled, "min_bucket": min_bucket},
            "intents": {"n_search": 0, "buckets": {}},
            "zero_result": {"n": 0, "n_search": 0, "examples": [], "by_bucket": {}},
            "abandonment": {"status": "no_access_data", "n_abandoned": 0, "n_considered": 0,
                            "n_pre_hook": 0, "ts_domain": False, "examples": [], "by_bucket": {}},
            "arm_wins": {"overall": {"n_labeled": 0, "sufficient": False,
                                     "wins": {a: 0.0 for a in ARM_NAMES},
                                     "no_arm_surfaced": 0, "rates": None},
                         "by_bucket": {}},
            "tuner": None}
    try:
        kept, undated = filter_since(records, since)
        data["window"]["n_undated"] = undated
        # the UNFILTERED size, so a --since that excludes everything is not rendered as "no log yet"
        data["window"]["n_total"] = sum(1 for r in (records or []) if isinstance(r, dict))
        searches = _searches(kept)
        ts_vals = sorted(r["ts"] for r in searches if isinstance(r.get("ts"), str))
        if ts_vals:
            data["window"]["ts_min"], data["window"]["ts_max"] = ts_vals[0], ts_vals[-1]
        data["counts"]["n_records"] = sum(1 for r in kept if isinstance(r, dict))
        data["counts"]["n_search"] = len(searches)
        data["counts"]["n_access_events"] = sum(
            1 for r in kept if isinstance(r, dict) and r.get("type") == "access")
        data["counts"]["n_redacted_queries"] = sum(
            1 for r in searches if isinstance(r.get("query"), str) and r["query"].startswith("redacted:"))
        try:
            labeled = _tw.build_qrels(kept)
        except Exception:
            labeled = []
        data["counts"]["n_labeled"] = len(labeled)
        data["sufficient"] = len(labeled) >= min_labeled
        data["intents"] = intent_mix(searches)
        data["zero_result"] = zero_result(searches)
        try:
            folded = _rl.fold_graded(kept)
        except Exception:
            folded = []
        data["abandonment"] = abandonment(kept, folded)
        data["arm_wins"] = arm_win_rates(labeled, min_labeled=min_labeled, min_bucket=min_bucket)
        if with_tuner:
            data["tuner"] = tuner_preview(kept, root, min_labeled=min_labeled, min_bucket=min_bucket)
    except Exception:
        pass
    return data


def insights_for_root(root, *, since=None, **kw) -> dict:
    """read_records + build_insights. Never raises."""
    try:
        records = _rl.read_records(root) if root is not None else []
    except Exception:
        records = []
    return build_insights(records, root=root, since=since, **kw)


# ---- plain-text rendering ---------------------------------------------------
def _fmt_wins(wins: dict) -> str:
    return ", ".join(f"{a} {wins.get(a, 0):g}" for a in ARM_NAMES)


def _fmt_rates(rates: dict) -> str:
    return ", ".join(f"{a} {rates.get(a, 0) * 100:.1f}%" for a in ARM_NAMES)


def render_text(data: dict) -> str:
    """The plain-text report. Pure formatting; never raises."""
    try:
        c = data.get("counts", {})
        th = data.get("thresholds", {})
        min_labeled = th.get("min_labeled", DEFAULT_MIN_LABELED)
        min_bucket = th.get("min_bucket", DEFAULT_MIN_BUCKET)
        lines = []
        root = data.get("root") or "."
        lines.append(f"deepmap insights  ({root})   [local, read-only — summarizes the opt-in "
                     f".deepmap/retrieval_log.jsonl; writes nothing, changes no ranking]")
        w = data.get("window", {})
        window = (f"   window {w.get('ts_min')}..{w.get('ts_max')}" if w.get("ts_min") else "")
        lines.append(f"  log: {c.get('n_records', 0)} records — {c.get('n_search', 0)} searches, "
                     f"{c.get('n_access_events', 0)} access events, {c.get('n_labeled', 0)} labeled "
                     f"(>=1 returned result later used){window}")
        if not c.get("n_records"):
            if w.get("since") and w.get("n_total"):
                lines.append(f"  0 of {w['n_total']} records match --since {w['since']} (a plain "
                             f"string compare against each record's ISO `ts`) — widen or drop --since")
            else:
                try:
                    logging_on = data.get("root") is not None and _rl.logging_enabled(Path(data["root"]))
                except Exception:
                    logging_on = False
                if logging_on:   # already enabled: "enable logging" would send the user in a circle
                    lines.append("  no retrieval log yet — logging is ON for this project; its searches "
                                 "are recorded from now on")
                else:
                    lines.append(f"  no retrieval log yet — enable logging with "
                                 f"{_rl.cli_command('learning --enable')} (or DEEPMAP_LEARNING=1 / "
                                 f"DEEPMAP_RETRIEVAL_LOG=1; docs/RM1-ENABLE.md)")
            return "\n".join(lines)
        if not data.get("sufficient"):
            lines.append(f"  !! insufficient data (n_labeled={c.get('n_labeled', 0)} < {min_labeled}): "
                         f"counts only, no conclusions — keep logging (docs/RM1-ENABLE.md)")
        if c.get("n_redacted_queries"):
            lines.append(f"  note: {c['n_redacted_queries']} queries stored redacted "
                         f"(DEEPMAP_LOG_REDACT); intent buckets were computed at log time")

        m = data.get("intents", {})
        lines.append(f"\n-- query intents (n={m.get('n_search', 0)} searches) --")
        buckets = m.get("buckets", {})
        for b in sorted(buckets, key=lambda x: -buckets[x].get("n", 0)):
            row = buckets[b]
            ex = row.get("examples") or []
            extra = f'   e.g. "{ex[0]}"' if ex else (
                "   (queries stored redacted — DEEPMAP_LOG_REDACT)" if row.get("n_redacted") else "")
            lines.append(f"  {b:13s} {row.get('n', 0):4d}{extra}")

        z = data.get("zero_result", {})
        lines.append("\n-- zero-result searches --")
        lines.append(f"  {z.get('n', 0)} of {z.get('n_search', 0)} searches returned nothing")
        for e in z.get("examples", []):
            lines.append(f'     "{e.get("query")}"   [{e.get("bucket")}]')

        ab = data.get("abandonment", {})
        lines.append("\n-- searches whose results were never used --")
        if ab.get("status") == "no_access_data":
            lines.append("  access capture: NO access events in the log — the opt-in PostToolUse "
                         "hook appears not to be registered (docs/RM1-ENABLE.md). Cannot "
                         "distinguish 'results never used' from 'usage not captured'; skipping "
                         "this section.")
        else:
            pre = (f"  ({ab.get('n_pre_hook', 0)} pre-hook searches excluded)"
                   if ab.get("n_pre_hook") else "")
            lines.append(f"  {ab.get('n_abandoned', 0)} of {ab.get('n_considered', 0)} searches "
                         f"with results earned no captured Read/Edit/impact{pre}")
            for e in ab.get("examples", []):
                lines.append(f'     "{e.get("query")}"   [{e.get("bucket")}]')
            lines.append("  (upper bound — access capture is opt-in and file-level)")

        aw = data.get("arm_wins", {})
        ov = aw.get("overall", {})
        lines.append(f"\n-- which arm first surfaced the result you used "
                     f"({ov.get('n_labeled', 0)} labeled searches) --")
        no_arm = f"   ({ov.get('no_arm_surfaced', 0)}: no arm surfaced it)" if ov.get("no_arm_surfaced") else ""
        if ov.get("rates"):
            lines.append(f"  {_fmt_rates(ov['rates'])}{no_arm}")
        else:
            lines.append(f"  counts only (n={ov.get('n_labeled', 0)} < {min_labeled}): "
                         f"{_fmt_wins(ov.get('wins', {}))}{no_arm}")
        bb = aw.get("by_bucket", {})
        for b in sorted(bb, key=lambda x: -bb[x].get("n_labeled", 0)):
            row = bb[b]
            if row.get("rates"):
                lines.append(f"  [{b:12s}] n={row.get('n_labeled', 0)}: {_fmt_rates(row['rates'])}")
            else:
                lines.append(f"  [{b:12s}] n={row.get('n_labeled', 0)}: counts only "
                             f"(insufficient, n < {min_bucket}) — {_fmt_wins(row.get('wins', {}))}")

        t = data.get("tuner")
        if t is not None:
            lines.append("\n-- what the tuner would produce today (dry-run; nothing written, "
                         "nothing applied) --")
            cur = t.get("current")
            if cur:
                lines.append(f"  current instance.json: weights {cur.get('arm_weights')}  "
                             f"(applied={cur.get('applied')}, reason={cur.get('reason')})")
            else:
                lines.append("  current instance.json: none yet")
            wb = t.get("would_be")
            if wb is None:
                lines.append("  would-be: unavailable (tuner error)")
            elif wb.get("reason") == "below_threshold":
                lines.append(f"  would-be: equal weights — reason: below_threshold "
                             f"(n_labeled={wb.get('n_labeled_queries', 0)} < {min_labeled}); equal stands")
            else:
                lines.append(f"  would-be: weights {wb.get('arm_weights')}  reason={wb.get('reason')}  "
                             f"beats_equal={wb.get('beats_equal')} significant={wb.get('significant')}")
                for b, p in (wb.get("profiles") or {}).items():
                    lines.append(f"     profile[{b}]: {p.get('arm_weights')}  (n={p.get('n_labeled')})")
            try:
                report_cmd = _rl.cli_command("tune --report")
            except Exception:
                report_cmd = "deepmap_cli.py tune --report"
            lines.append(f"  live-apply gate & benchmark evidence: {report_cmd}")
        return "\n".join(lines)
    except Exception:
        return "deepmap insights: report unavailable (malformed data)"
