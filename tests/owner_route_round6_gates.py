#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-network gates for ROUND-6 W1 — H4 (the hooks' heal-refusal classification in
hooks/deepmap_hook_state.py, plus the provision-side wording drift lock) and the port_registry support for
W2's H2/H3 (the Neo4j allocator never holds the registry lock across base_adopt_ok).

  H4  heal's status file carries `refused` (bool) and `refusal_kind` ("foreign-base-claim" | "legacy-owner" |
      null) since round 5. Those fields are AUTHORITATIVE: an explicit `refused: false` (or a present
      `refusal_kind` key) decides, and the phrase fallback (_REFUSAL_WORDS — "can't be shown to be this
      project's", "belongs to project", "holds project") applies ONLY to a status that carries neither key (a
      round-4-era file). Before, the fallback ran whatever `refused` said, and heal's own NON-policy legacy
      failures used exactly that phrase ("the Neo4j at X is not reachable (...), so its graph can't be shown to
      be this project's" / "... rejected DeepMap's credentials, so its graph can't be shown ..."), written with
      refused:false. A transient outage / login lock between _bolt_state and _legacy_route_verdict then read as
      a FINAL policy refusal: notify said DeepMap "must NOT be used ... could come from ANOTHER project's
      graph", the post-edit sync flag went silent, and no probe could ever clear it.
        A  _status_is_refusal truth table (explicit fields decide; the fallback only for key-less statuses;
           a contradictory refused:false + a known refusal_kind stays a refusal — the safe side).
        B  end to end on the legacy route: such a status is a plain "neo4j" failure — notify shows the
           "not reachable" line (never the refusal line), the post-edit flag still fires, the nudge stays
           silent; controls: refused:true and a round-4 key-less refusal are still refusals.
        C  heal_problem_superseded no longer lets the reason's wording veto a probe for a status whose
           fields said "not a refusal"; a direct call on a key-less problem keeps the round-5 behaviour.
        D  provision drift lock: heal's legacy connect ("down") and credentials ("auth") failure texts from
           the REAL provision._legacy_route_verdict are not refusal-worded (H4's provision half), while its
           "refused" text still is.

  E  (support for W2's H2/H3) allocate_neo4j_ports asks `base_adopt_ok` BEFORE taking the registry lock.
     That callback is provision._base_adopt_ok: Neo4j I/O (a bolt probe + a live :_DeepMapMeta owner read of
     the base — seconds; H2/H3b re-verify the base owner on exactly this path). Held under the registry lock,
     it made every other project's allocation wait for the whole call (and fail after _ALLOC_LOCK_WAIT_S:
     "registry busy"). It also made any registry writer the callback reaches (a last_used touch, an
     allocation) block on its own process's lock. The claimed set is re-read under the lock, so a base another project claimed
     meanwhile is still never handed out. Asked exactly when the scan would ask it (never for a base another
     project claims, never when `prefer` or an existing pair decides), once; the rare "free only under the
     lock" case still asks it there.

  F  commands/setup.md (W1-owned) tells the agent what setup now does in W2's two upgrade cases: an older
     build's MCP server on this project's port is restarted too (H1), and the FAILED line setup prints when it
     could not restart one (quoted verbatim, drift-locked against provision) is relayed with "stop it, re-run
     setup" and never "reload"; a graph on the shared 7687 store is kept when provably this project's, a DOWN
     store with free ports is started only to read its owner, and otherwise the project gets its own
     instance (H2/H3).

Hermetic: temp git repos + a temp DEEPMAP_HOME (helpers shared with tests/owner_route_round5_gates.py);
the port probes stubbed; provision's driver factory stubbed; no Neo4j, no sockets, no network. Exit 0 = green.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("dmr6_owner_route_r5", _TESTS / "owner_route_round5_gates.py")
R5 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R5)          # helpers only (its main() runs under __main__ alone)
PR = R5.PR

_PASS = 0
_FAIL = 0

# What a round-5 heal wrote for the legacy route's NON-policy failures (refused: false) — the exact texts
# provision._legacy_route_verdict produced, so a status already on disk keeps being read correctly whatever
# provision's wording becomes.
URI = "bolt://localhost:7687"
DOWN_R5 = (f"the Neo4j at {URI} is not reachable (ServiceUnavailable), so its graph can't be shown to be this "
           "project's — this project has no DeepMap Neo4j instance of its own: run /deep-map:setup to give it one")
AUTH_R5 = (f"the Neo4j at {URI} rejected DeepMap's credentials, so its graph can't be shown to be this project's "
           "— this project has no DeepMap Neo4j instance of its own: run /deep-map:setup to give it one")
F2 = R5.F2_REASON
LEGACY = R5.LEGACY_REASON
UNOWNED = (f"{URI} holds a DeepMap graph with no recorded owner, so it can't be shown to be this project's — "
           "this project has no DeepMap Neo4j instance of its own (never set up here, or removed by instance-rm) "
           "— run /deep-map:setup in this project to give it one")


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:400]) if detail else ''}")


def _hs():
    return R5._load_hook("deepmap_sessionstart_notify")._hs


# --------------------------------------------------------------------------- A
def gate_a_truth_table() -> None:
    print("\n-- A: _status_is_refusal — the refused / refusal_kind fields decide; wording only without them --")
    hs = _hs()
    st = getattr(hs, "_status_is_refusal", None)
    if not callable(st):
        check("deepmap_hook_state._status_is_refusal exists", False)
        return
    base = {"neo4j_ok": False, "mcp_ok": False}
    not_refusals = [
        ("refused:false + refusal_kind:null + heal's legacy DOWN text", dict(refused=False, refusal_kind=None,
                                                                          reason=DOWN_R5)),
        ("refused:false + refusal_kind:null + heal's legacy AUTH text", dict(refused=False, refusal_kind=None,
                                                                          reason=AUTH_R5)),
        ("refused:false alone + an F2-worded reason", dict(refused=False, reason=F2)),
        ("refused:false alone + a legacy-owner-worded reason", dict(refused=False, reason=LEGACY)),
        ("refusal_kind:null alone (a present key) + a refusal-worded reason", dict(refusal_kind=None, reason=UNOWNED)),
        ("refused:false + an unknown refusal_kind + a refusal-worded reason",
         dict(refused=False, refusal_kind="something-else", reason=F2)),
        ("key-less (round-4) status whose reason is plain 'not reachable'",
         dict(reason="bolt://localhost:7687 not reachable after 120s")),
        ("key-less status with neo4j_ok true (mcp failure) and a refusal-worded reason",
         dict(neo4j_ok=True, reason=F2)),
    ]
    for label, extra in not_refusals:
        d = dict(base, **extra)
        check(f"NOT a refusal: {label}", st(d) is False, json.dumps(d)[:200])
    refusals = [
        ("refused:true + foreign-base-claim", dict(refused=True, refusal_kind="foreign-base-claim", reason=F2)),
        ("refused:true + legacy-owner", dict(refused=True, refusal_kind="legacy-owner", reason=LEGACY)),
        ("refused:true with no reason at all", dict(refused=True, refusal_kind=None, reason=None)),
        ("a known refusal_kind alone (no 'refused' key)", dict(refusal_kind="legacy-owner", reason=None)),
        ("contradictory refused:false + a known refusal_kind (the safe side)",
         dict(refused=False, refusal_kind="foreign-base-claim", reason=None)),
        ("key-less (round-4) status whose reason is the F2 refusal", dict(reason=F2)),
        ("key-less (round-4) status whose reason is the legacy-owner refusal", dict(reason=LEGACY)),
        ("key-less (round-4) status whose reason is the unowned-graph refusal", dict(reason=UNOWNED)),
    ]
    for label, extra in refusals:
        d = dict(base, **extra)
        check(f"a refusal: {label}", st(d) is True, json.dumps(d)[:200])
    for bad in (None, [], "refused", 7, {"refused": True, "__class__": 1}):
        try:
            got = st(bad)
            ok = got in (True, False) and (got is True) == (isinstance(bad, dict) and bad.get("refused") is True)
        except Exception as exc:  # noqa: BLE001
            ok, got = False, f"raised {exc!r}"
        check(f"never raises / sane on {bad!r}", ok, str(got))


# --------------------------------------------------------------------------- B
def gate_b_end_to_end_legacy_route() -> None:
    print("\n-- B: end to end on the legacy route — a non-policy failure is a plain outage, not a refusal --")
    NT = R5._load_hook("deepmap_sessionstart_notify")
    NU = R5._load_hook("deepmap_presearch_nudge")
    SF = R5._load_hook("deepmap_postedit_syncflag")
    hs = NT._hs
    UP = {18911, 18912, 7687}   # everything listens: a probe must not be what decides here
    for i, (label, reason) in enumerate((("legacy DOWN", DOWN_R5), ("legacy AUTH", AUTH_R5))):
        with R5._Home() as h:
            r = R5._hook_project(h, bolt=None)           # MCP pair only -> the legacy-unverified 7687 route
            route = PR.resolve_neo4j_route(r)
            check(f"[{label}] precondition: the project's route is legacy-unverified",
                  route.source == "legacy-unverified", repr(route))
            R5._status(h, r, neo4j_ok=False, mcp_ok=False, refused=False, refusal_kind=None, reason=reason,
                       mcp_stopped=[], mcp_unverified=[])
            saved = hs.port_listening
            hs.port_listening = lambda port, host="127.0.0.1", timeout=None: int(port) in UP
            try:
                prob = hs.heal_problem(r)
                prob_nr = hs.heal_problem(r, recheck=False)
            finally:
                hs.port_listening = saved
            check(f"[{label}] heal_problem -> ('neo4j', reason), not 'refused'",
                  isinstance(prob, tuple) and prob[0] == "neo4j", str(prob))
            check(f"[{label}] heal_problem(recheck=False) -> 'neo4j' too", isinstance(prob_nr, tuple)
                  and prob_nr[0] == "neo4j", str(prob_nr))
            ctx = R5._ctx(R5._InProc(NT, h.home, UP)({"cwd": str(r), "session_id": f"n{i}"}))
            check(f"[{label}] notify: the 'NOT reachable' outage line (names /deep-map:setup), never the "
                  "'must NOT be used / ANOTHER project's graph' refusal line",
                  "NOT reachable" in ctx and "/deep-map:setup" in ctx and "must NOT be used" not in ctx
                  and "ANOTHER project" not in ctx and "is ACTIVE" not in ctx, ctx[:300])
            sctx = R5._ctx(R5._InProc(SF, h.home, UP)({"cwd": str(r), "session_id": f"s{i}", "tool_name": "Edit",
                                                       "tool_input": {"file_path": str(r / "a.py")}}))
            check(f"[{label}] post-edit sync flag still fires (/deep-map:refresh) — only a policy refusal "
                  "silences it", "/deep-map:refresh" in sctx, sctx[:200])
            nctx = R5._ctx(R5._InProc(NU, h.home, UP)({"cwd": str(r), "session_id": f"u{i}", "tool_name": "Grep",
                                                       "tool_input": {"pattern": "x"}}))
            check(f"[{label}] nudge stays silent (a recorded outage never steers to DeepMap)", nctx == "", nctx[:160])
    # controls: a real refusal (either shape) is still final on the same project
    for i, (label, fields) in enumerate((
            ("refused:true legacy-owner", dict(refused=True, refusal_kind="legacy-owner", reason=LEGACY)),
            ("key-less round-4 refusal", dict(reason=LEGACY)))):
        with R5._Home() as h:
            r = R5._hook_project(h, bolt=None)
            R5._status(h, r, neo4j_ok=False, mcp_ok=False, **fields)
            ctx = R5._ctx(R5._InProc(NT, h.home, UP)({"cwd": str(r), "session_id": f"c{i}"}))
            check(f"control [{label}]: notify still shows the refusal line", "must NOT be used" in ctx
                  and "/deep-map:setup" in ctx, ctx[:300])
            sctx = R5._ctx(R5._InProc(SF, h.home, UP)({"cwd": str(r), "session_id": f"cs{i}", "tool_name": "Edit",
                                                       "tool_input": {"file_path": str(r / "a.py")}}))
            check(f"control [{label}]: post-edit sync flag still silent", sctx == "", sctx[:160])


# --------------------------------------------------------------------------- C
def gate_c_superseded_honours_the_fields() -> None:
    print("\n-- C: heal_problem_superseded — wording never vetoes a probe for a status whose fields say 'no' --")
    NT = R5._load_hook("deepmap_sessionstart_notify")
    hs = NT._hs
    OWN = (7811, 7598)
    UP = {18911, 18912, OWN[0]}
    worded = DOWN_R5.replace(URI, f"bolt://localhost:{OWN[0]}")
    cases = [
        ("neo4j failure, refused:false, refusal-worded reason, every port up", True,
         dict(neo4j_ok=False, mcp_ok=False, refused=False, refusal_kind=None, reason=worded), UP),
        ("mcp failure, refused:false, refusal-worded reason, both MCP ports up", True,
         dict(neo4j_ok=True, mcp_ok=False, refused=False, refusal_kind=None, reason=worded), UP),
        ("control: neo4j failure, refused:false, its bolt port DOWN -> the failure stands", False,
         dict(neo4j_ok=False, mcp_ok=False, refused=False, refusal_kind=None, reason=worded), {18911, 18912}),
        ("control: key-less (round-4) status with that wording -> still a refusal, never superseded", False,
         dict(neo4j_ok=False, mcp_ok=False, reason=worded), UP),
        ("control: key-less mcp failure with that wording -> the wording still vetoes the probe", False,
         dict(neo4j_ok=True, mcp_ok=False, reason=worded), UP),
    ]
    for label, superseded, fields, up in cases:
        with R5._Home() as h:
            r = R5._hook_project(h, bolt=OWN)            # its OWN per-project instance: bolt_target vouches
            check(f"[{label}] precondition: bolt_target is the project's own instance",
                  hs.bolt_target(r) == ("127.0.0.1", OWN[0]), str(hs.bolt_target(r)))
            R5._status(h, r, **fields)
            saved = hs.port_listening
            hs.port_listening = lambda port, host="127.0.0.1", timeout=None, _up=up: int(port) in _up
            try:
                prob = hs.heal_problem(r)
            finally:
                hs.port_listening = saved
            check(f"{label}: heal_problem -> {'None (superseded)' if superseded else 'kept'}",
                  (prob is None) if superseded else (prob is not None), str(prob))
    sup = getattr(hs, "heal_problem_superseded", None)
    with R5._Home() as h:
        r = R5._hook_project(h, bolt=OWN)
        saved = hs.port_listening
        hs.port_listening = lambda port, host="127.0.0.1", timeout=None: int(port) in UP
        try:
            check("a direct call on a refusal-worded ('neo4j', reason) problem keeps the round-5 answer (False)",
                  callable(sup) and sup(r, ("neo4j", F2)) is False and sup(r, ("neo4j", worded)) is False)
            check("('refused', reason) is never superseded, whatever the caller says about the fields",
                  callable(sup) and _try_kw(sup, r, ("refused", worded)) is False)
        finally:
            hs.port_listening = saved


def _try_kw(sup, r, problem):
    """heal_problem_superseded with the fields flag when it exists (the fixed signature), else plainly."""
    for kw in ("refusal_fields",):
        try:
            return sup(r, problem, **{kw: True})
        except TypeError:
            continue
    return sup(r, problem)


# --------------------------------------------------------------------------- D
class ServiceUnavailable(Exception):   # the neo4j driver's class name for "nothing answered"
    pass


class AuthError(Exception):            # provision._is_auth_error matches the class name
    pass


class ClientError(Exception):          # Neo4j's login lock arrives as a plain ClientError with this code
    code = "Neo.ClientError.Security.AuthenticationRateLimit"


class _FakeDriver:
    def __init__(self, exc):
        self.exc = exc

    def verify_connectivity(self):
        raise self.exc

    def close(self):
        pass


def gate_d_provision_wording() -> None:
    print("\n-- D: provision drift lock — heal's legacy down / auth texts are not refusal-worded (H4) --")
    hs = _hs()
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            import provision as P  # noqa: WPS433
    except Exception as exc:  # noqa: BLE001
        check("provision importable", False, repr(exc))
        return
    lrv = getattr(P, "_legacy_route_verdict", None)
    if not callable(lrv):
        check("provision._legacy_route_verdict exists", False)
        return
    with R5._Home() as h:
        repo = R5._repo(h.work, "proj")
        saved = P._make_driver
        got = {}
        try:
            for name, exc in (("down", ServiceUnavailable("connection refused")),
                              ("auth", AuthError("The client is unauthorized due to authentication failure.")),
                              ("auth-locked", ClientError("The client has provided incorrect authentication "
                                                          "details too many times in a row."))):
                P._make_driver = lambda *a, _e=exc, **k: _FakeDriver(_e)
                with contextlib.redirect_stderr(io.StringIO()):
                    got[name] = lrv(repo, URI, "neo4j", "not-the-password")
        finally:
            P._make_driver = saved
        for name in ("down", "auth", "auth-locked"):
            state, text = got.get(name) or (None, None)
            want = "auth" if name.startswith("auth") else name
            check(f"precondition: _legacy_route_verdict -> state {want!r} ({name})", state == want,
                  str(got.get(name)))
            if want == "auth":
                check(f"heal's legacy {name!r} text keeps a credentials word (the hooks never let a port probe "
                      "supersede a credentials rejection)",
                      bool(text) and any(w in str(text).lower() for w in hs._AUTH_WORDS), str(text)[:300])
            check(f"heal's legacy {name!r} text does not reuse refusal phrasing (so even a key-less reader can't "
                  "take it for a policy refusal)", bool(text) and not hs._reason_is_refusal(text), str(text)[:300])
            check(f"heal's legacy {name!r} text still names the fix (/deep-map:setup)",
                  bool(text) and "/deep-map:setup" in str(text), str(text)[:300])
        # the POLICY refusal text on the same route must stay recognisable by its wording (round-5 lock)
        fct = getattr(P, "_foreign_claim_text", None)
        other = "C:/w/other" if os.name == "nt" else "/w/other"
        refusal_texts = [PR.legacy_owner_verdict(repo, other, True)[1], PR.legacy_owner_verdict(repo, None, True)[1]]
        if callable(fct):
            refusal_texts.append(fct(other, URI, "its owner record"))
        check("the real refusal texts are still recognised by their wording",
              all(t and hs._reason_is_refusal(t) for t in refusal_texts), str(refusal_texts)[:300])


# --------------------------------------------------------------------------- E
def gate_e_allocator_asks_base_adopt_ok_unlocked() -> None:
    print("\n-- E: allocate_neo4j_ports asks base_adopt_ok OUTSIDE the registry lock (W2 H2/H3 support) --")
    BASE, NEXT = (PR.BASE_BOLT, PR.BASE_HTTP), (PR.BASE_BOLT + PR.STRIDE, PR.BASE_HTTP + PR.STRIDE)

    # E1 the lock is free while the callback runs (a Neo4j start there must not stall every other setup)
    with R5._Home() as h:
        a = R5._repo(h.work, "a")
        seen = []

        def cb_lock_free():
            hd = PR._acquire_lock(wait_s=0)
            seen.append(hd is not None)
            PR._release_lock(hd)
            return True

        got = R5._try(PR.allocate_neo4j_ports, a, base_adopt_ok=cb_lock_free)
        check("E1 base_adopt_ok runs with the registry lock FREE (another setup can allocate meanwhile)",
              seen == [True], f"lock free during the callback: {seen}")
        check("E1 ...and its True still adopts the base (zero drift for the first project)", got == BASE, str(got))

    # E2 a registry writer the callback reaches (a last_used touch) lands and is not lost by the allocation
    with R5._Home() as h:
        a = R5._repo(h.work, "a")
        ka = PR.project_key(a)
        h.register({ka: R5._entry(a, mcp=(8787, 8788))})           # MCP pair only, last_used long ago
        old = h.registry()["projects"][ka]["last_used"]

        def cb_touch():
            PR.touch_last_used(a)
            return True

        got = R5._try(PR.allocate_neo4j_ports, a, base_adopt_ok=cb_touch)
        ent = h.registry().get("projects", {}).get(ka, {})
        check("E2 a last_used touch from inside base_adopt_ok lands (it used to wait out the lock and give up)",
              ent.get("last_used") not in (None, old), f"last_used {old!r} -> {ent.get('last_used')!r}")
        check("E2 ...and the allocation merged on top of it (MCP pair kept, bolt pair recorded)",
              got == BASE and (ent.get("search_port"), ent.get("embed_port")) == (8787, 8788)
              and (ent.get("bolt_port"), ent.get("http_port")) == BASE, f"{got} {ent}")

    # E3 the race: while base_adopt_ok ran, ANOTHER project claimed the base -> re-checked under the lock
    with R5._Home() as h:
        a = R5._repo(h.work, "a")
        b = R5._repo(h.work, "b")
        kb = PR.project_key(b)
        wrote = []

        def cb_race():
            hd = PR._acquire_lock(wait_s=0)
            if hd is None:
                wrote.append(False)
                return True
            try:
                reg = PR.load_registry() or {"version": PR.REGISTRY_VERSION, "kind": PR.REGISTRY_KIND,
                                             "projects": {}}
                reg["projects"][kb] = R5._entry(b, mcp=(8789, 8790), bolt=BASE)
                wrote.append(PR._write_registry(reg))
            finally:
                PR._release_lock(hd)
            return True

        got = R5._try(PR.allocate_neo4j_ports, a, base_adopt_ok=cb_race)
        ents = h.registry().get("projects", {})
        check("E3 precondition: the concurrent claim landed while base_adopt_ok ran", wrote == [True], str(wrote))
        check("E3 a base claimed meanwhile by another project is NOT handed out (re-checked under the lock) -> "
              "the next pair", got == NEXT, str(got))
        check("E3 ...and the other project's claim survives the allocation's write",
              (ents.get(kb, {}).get("bolt_port"), ents.get(kb, {}).get("http_port")) == BASE, str(ents.get(kb)))

    # E4 asked exactly when the scan would ask it — once — and never otherwise (regression locks)
    with R5._Home() as h:
        a = R5._repo(h.work, "a")
        calls = []
        got = R5._try(PR.allocate_neo4j_ports, a, base_adopt_ok=lambda: calls.append(1) or False)
        check("E4 its False -> the base is skipped, the next pair (asked once)", got == NEXT and calls == [1],
              f"{got} calls={calls}")
        calls.clear()
        got = R5._try(PR.allocate_neo4j_ports, a, base_adopt_ok=lambda: calls.append(1) or True)
        check("E4 already allocated -> the existing pair, not asked", got == NEXT and calls == [], f"{got} {calls}")
    with R5._Home() as h:
        a = R5._repo(h.work, "a")
        b = R5._repo(h.work, "b")
        h.register({PR.project_key(b): R5._entry(b, mcp=(8789, 8790), bolt=BASE)})
        calls = []
        got = R5._try(PR.allocate_neo4j_ports, a, base_adopt_ok=lambda: calls.append(1) or True)
        check("E4 a base another project claims (no owner record) -> not asked, the next pair",
              got == NEXT and calls == [], f"{got} calls={calls}")
    with R5._Home() as h:
        a = R5._repo(h.work, "a")
        calls = []
        got = R5._try(PR.allocate_neo4j_ports, a, prefer=(7811, 7598), base_adopt_ok=lambda: calls.append(1) or True)
        check("E4 an adoptable `prefer` pair decides -> not asked", got == (7811, 7598) and calls == [],
              f"{got} calls={calls}")

    # E5 the rare fallback: the pre-lock read saw the base claimed, the claim was released before we got the
    # lock -> base_adopt_ok is asked under the lock (once) and its answer used, exactly as before
    with R5._Home() as h:
        a = R5._repo(h.work, "a")
        b = R5._repo(h.work, "b")
        kb = PR.project_key(b)
        h.register({kb: R5._entry(b, mcp=(8789, 8790), bolt=BASE)})
        saved = PR._acquire_lock
        released = []

        def acquire_after_release(env=None, wait_s=None):
            if not released:                                   # the allocation's lock: B released meanwhile
                ents = h.registry().get("projects", {})
                for f in ("bolt_port", "http_port", "neo4j_allocated_utc"):
                    ents[kb].pop(f, None)
                h.register(ents)
                released.append(True)
            return saved(env, wait_s)

        calls = []
        PR._acquire_lock = acquire_after_release
        try:
            got = R5._try(PR.allocate_neo4j_ports, a, base_adopt_ok=lambda: calls.append(1) or True)
        finally:
            PR._acquire_lock = saved
        check("E5 claimed at the pre-lock read, free under the lock -> asked there once; its True adopts the base",
              released == [True] and got == BASE and calls == [1], f"{got} calls={calls} released={released}")

    # E7 the G4 case (H2's re-adoption): another project claims the base, but the base owner record names THIS
    # project -> the claim is void for it; base_adopt_ok (the live owner re-read) is asked once, lock free
    with R5._Home() as h:
        y = R5._repo(h.work, "owner")
        x = R5._repo(h.work, "claimant")
        h.register({PR.project_key(x): R5._entry(x, mcp=(8789, 8790), bolt=BASE)})
        h.record(y)
        seen = []

        def cb_void():
            hd = PR._acquire_lock(wait_s=0)
            seen.append(hd is not None)
            PR._release_lock(hd)
            return True

        got = R5._try(PR.allocate_neo4j_ports, y, base_adopt_ok=cb_void)
        check("E7 a claim void for this project (owner record names it): asked once, with the lock FREE -> the "
              "real owner re-adopts the base", got == BASE and seen == [True], f"{got} lock-free={seen}")

    # E6 the MCP pool never has a callback: byte-identical scan (clean machine -> the base MCP pair)
    with R5._Home() as h:
        a = R5._repo(h.work, "a")
        check("E6 MCP pool unchanged (clean machine -> (8787, 8788))", R5._try(PR.allocate_ports, a) == (8787, 8788))


# --------------------------------------------------------------------------- F
def gate_f_setup_doc() -> None:
    """commands/setup.md tells the agent what setup now does for the two upgrade cases W2 fixed (H1, H2/H3), in
    words that match the provisioner's own output."""
    print("\n-- F: commands/setup.md explains the pre-upgrade server restart (H1) and the legacy store (H2/H3) --")
    t = " ".join((R5.COMMANDS / "setup.md").read_text(encoding="utf-8").split())
    low = t.lower()
    sents = re.split(r"(?<=[.!?])\s+", t)

    def para(*words):
        return next((s for s in sents if all(w.lower() in s.lower() for w in words)), "")

    s = para("older DeepMap", "restart")
    check("H1: a server an OLDER DeepMap build started on this project's port is restarted too",
          bool(s), "no sentence names an older build's server being restarted")
    blocked = "still held by a DeepMap server setup could not restart"
    check("H1: the FAILED line setup prints when it could not restart such a server is quoted verbatim",
          blocked in t, "missing: " + blocked)
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            import provision as P  # noqa: WPS433
        src = Path(P.__file__).read_text(encoding="utf-8")
        joined = re.sub(r"\"\s*\n\s*f?\"", "", src)          # adjacent string literals across a line break
        check("H1: ...and that wording is what provision really prints (drift lock)",
              blocked in joined and "setup verdict: FAILED" in joined)
    except Exception as exc:  # noqa: BLE001
        check("provision importable for the setup.md drift lock", False, repr(exc))
    s = para("could not restart", "reload")
    check("H1: for that FAILED line the agent relays it, has the user stop the process + re-run setup, and does "
          "NOT tell them to reload", bool(s) and "not" in s.lower() and "/deep-map:setup" in t
          and "stop" in s.lower(), s[:300])
    s = para("7687", "older DeepMap", "own")
    check("H2/H3: a project whose graph is on the shared 7687 store keeps it when it is provably its own",
          bool(s), "no sentence explains the legacy 7687 store")
    check("H3: a DOWN legacy store with free ports is started only to read whose graph it holds, and stopped "
          "again when it is not this project's",
          "starts it" in low and "stops it again" in low, "")
    check("H2/H3: otherwise (another project's graph / 7687 held by another program) the project gets its own "
          "instance and its graph is rebuilt there, the old store left for its owner",
          "rebuilt there" in low and "left untouched" in low, "")
    # round-5 hand-off (W3 -> W1): doctor's WARN for an MCP server that is UP on a refused base-store claim
    d = " ".join((R5.COMMANDS / "doctor.md").read_text(encoding="utf-8").split())
    warn = "started on this project's base-store claim refused above"
    s = next((x for x in re.split(r"(?<=[.!?])\s+", d) if warn in x), "")
    check("doctor.md: the 'MCP ... UP — but it was started on this project's base-store claim refused above' WARN "
          "is explained: its answers are another project's graph, never this one's; fix = /deep-map:setup",
          bool(s) and "another project" in d[d.find(warn):d.find(warn) + 500]
          and "/deep-map:setup" in d[d.find(warn):d.find(warn) + 500], s[:300])
    try:
        cli = (R5.REPO / "build" / "deepmap_cli.py").read_text(encoding="utf-8")
        cli_joined = re.sub(r"\"\s*\n\s*f?\"", "", cli)
        check("doctor.md: ...quoting the CLI's real wording (drift lock)", warn in cli_joined)
    except Exception as exc:  # noqa: BLE001
        check("build/deepmap_cli.py readable for the doctor.md drift lock", False, repr(exc))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — round-6 W1: heal refusal fields are authoritative (H4); base_adopt_ok off the lock")
    print(f"(hooks under test: {R5.HOOKS}; build: {R5.BUILD})")
    print("=" * 68)
    if not shutil.which("git"):
        print("RESULT: GREEN — 0/0 gates passed. (git not on PATH: skipped)")
        return 0
    gate_a_truth_table()
    gate_b_end_to_end_legacy_route()
    gate_c_superseded_honours_the_fields()
    gate_d_provision_wording()
    gate_e_allocator_asks_base_adopt_ok_unlocked()
    gate_f_setup_doc()
    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
