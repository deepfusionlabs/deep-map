#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for the RM-1 dogfood friction-reducer (one-command enable + accrual line).

The RM-1 learning loop is fully built but G4 is data-blocked (0 labeled retrieval logs; target 150).
This surface makes accrual EASY (one `deepmap learning --enable`) and VISIBLE (an "X/150 labeled
retrieval logs" line in `deepmap status` / `doctor`). It does NOT activate learned weights — the
retriever stays byte-frozen and never reads them.

Locks:
  - labeled_fragment / accrual_line: the shared 'X/150 labeled retrieval logs' wording (0/150 when off,
    N/150 after real access labels, ?/150 when the tuner is unavailable); never raises,
  - learning_status carries target_labeled (=DEFAULT_MIN_LABELED) and the honest labeled count,
  - format_status_line (doctor + `tune --status`) embeds the accrual fragment AND keeps the raw counts,
  - `deepmap learning --enable` writes the DURABLE instance flag + prints the opt-in access-hook snippet;
    --disable flips it; --status/no-flag prints the honest one-line snapshot,
  - `deepmap status` emits the accrual line BEFORE the Neo4j connect (visible even when the DB is down).
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
CLI = BUILD / "deepmap_cli.py"
PY = sys.executable

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


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"dm_{name}", BUILD / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def _run(args, cwd):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("DEEPMAP_LEARNING", None)   # a stray env must not mask the durable-flag behaviour
    return subprocess.run([PY, str(CLI), *args], cwd=str(cwd), capture_output=True, text=True,
                          env=env, timeout=90)


def _seed_labeled_log(root: Path, L, n=8):
    """8 searches whose accessed 'p' is later Edited -> 8 labeled queries (the learning_loop idiom)."""
    per_arm = {"vector": ["x", "p"], "bm25": ["p", "x"], "graph_weighted": ["x", "p"]}
    for i in range(n):
        returned = [{"id": rid, "file": f"f/{rid}.py", "name": rid, "line": 1} for rid in ("x", "p")]
        L.append_record(root, L.build_record(f"q{i}", per_arm, returned, ts=f"t{i}"))
        L.append_record(root, {"type": "access", "v": 2, "paths": ["f/p.py"], "symbols": [],
                               "kind": "edit", "ts": f"t{i}b"})


def gate_helpers(LL) -> None:
    print("\n-- labeled_fragment / accrual_line: the shared wording, never raises --")
    check("fragment None -> ?/150", LL.labeled_fragment(None) == "?/150 labeled retrieval logs",
          LL.labeled_fragment(None))
    check("fragment 8 -> 8/150", LL.labeled_fragment(8) == "8/150 labeled retrieval logs",
          LL.labeled_fragment(8))
    check("fragment honours a custom target", LL.labeled_fragment(8, 200) == "8/200 labeled retrieval logs",
          LL.labeled_fragment(8, 200))
    with tempfile.TemporaryDirectory() as td:
        check("accrual on empty root -> 0/150 (honest zero)",
              LL.accrual_line(Path(td)) == "0/150 labeled retrieval logs", LL.accrual_line(Path(td)))
    raised = False
    try:
        out = LL.accrual_line("\x00::bad::")
    except Exception:
        raised = True
        out = "raised"
    check("accrual on a bad root -> no raise, still a fragment", (not raised) and "labeled retrieval logs" in out, out)


def gate_status_and_format(LL, L) -> None:
    print("\n-- learning_status.target_labeled + labeled count + format_status_line accrual --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _seed_labeled_log(root, L)
        st = LL.learning_status(root, env={})
        check("target_labeled == 150 (the cold-start threshold)", st.get("target_labeled") == 150,
              str(st.get("target_labeled")))
        check("labeled count == 8 (folded access labels)", st.get("n_labeled") == 8, str(st.get("n_labeled")))
        check("accrual line reads 8/150", LL.accrual_line(root) == "8/150 labeled retrieval logs",
              LL.accrual_line(root))
    # The ONE line doctor + `tune --status` share now embeds the accrual AND keeps the raw counts.
    on = {"enabled": True, "source": "env", "n_records": 212, "n_labeled": 167,
          "active": False, "inactive_reasons": ["env DEEPMAP_APPLY_LEARNED not set"]}
    _s, detail = LL.format_status_line(on)
    check("doctor line embeds '167/150 labeled retrieval logs'",
          "167/150 labeled retrieval logs" in detail, detail)
    check("doctor line still carries the raw events count (drift-lock with learning_loop_gates)",
          "212" in detail and "167" in detail, detail)
    _s, detail = LL.format_status_line(dict(on, n_labeled=None))
    check("unknown labeled count -> '?/150' (no crash)", "?/150 labeled retrieval logs" in detail, detail)


def gate_cli_enable_disable_status() -> None:
    print("\n-- deepmap learning --enable/--disable/--status (subprocess; no Neo4j) --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # --status BEFORE enabling: OFF + honest zero.
        p = _run(["learning", "--status"], root)
        check("pre-enable --status exits 0", p.returncode == 0, p.stderr.strip())
        check("pre-enable shows OFF + 0/150", "OFF" in p.stdout and "0/150 labeled retrieval logs" in p.stdout,
              p.stdout.strip())

        # --enable: durable flag + hook snippet.
        p = _run(["learning", "--enable"], root)
        check("--enable exits 0", p.returncode == 0, p.stderr.strip())
        inst = root / ".deepmap" / "instance.json"
        flag = None
        if inst.is_file():
            flag = json.loads(inst.read_text(encoding="utf-8")).get("learned_fusion", {}).get("learning_enabled")
        check("--enable writes the DURABLE learning flag", flag is True, str(flag))
        check("--enable prints the opt-in access-hook path", "deepmap_access_hook.py" in p.stdout, p.stdout[-400:])
        check("--enable prints the canonical PostToolUse matcher",
              "Read|Edit|Write|.*deepmap_impact" in p.stdout, p.stdout[-400:])
        check("--enable prints the accrual line", "labeled retrieval logs" in p.stdout, p.stdout[-200:])
        check("--enable states live ranking is unchanged (no false learned-weights claim)",
              "UNCHANGED" in p.stdout or "equal-weight" in p.stdout, p.stdout[-400:])

        # --status AFTER enabling: ON via the durable instance flag.
        p = _run(["learning", "--status"], root)
        check("post-enable --status shows ON (source: instance)",
              "ON" in p.stdout and "instance" in p.stdout, p.stdout.strip())

        # --disable flips it back.
        p = _run(["learning", "--disable"], root)
        flag2 = json.loads(inst.read_text(encoding="utf-8")).get("learned_fusion", {}).get("learning_enabled")
        check("--disable exits 0 + flips the flag false", p.returncode == 0 and flag2 is False, str(flag2))


def gate_disable_env_caveat(LL) -> None:
    """`learning --disable` must not tell a user learning is off when a truthy DEEPMAP_LEARNING in the
    env still forces it ON (retrieval_log.learning_enabled checks the env FIRST). The --enable branch
    already surfaces the reciprocal caveat; --disable must too. RED-first: without the caveat print the
    'warns the env still forces learning ON' check fails."""
    print("\n-- `learning --disable` warns when DEEPMAP_LEARNING=1 in env still forces learning ON --")

    def _run_env(args, cwd, learning_env):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        if learning_env is None:
            env.pop("DEEPMAP_LEARNING", None)
        else:
            env["DEEPMAP_LEARNING"] = learning_env
        return subprocess.run([PY, str(CLI), *args], cwd=str(cwd), capture_output=True, text=True,
                              env=env, timeout=90)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # (a) DEEPMAP_LEARNING=1 present -> --disable SURFACES the env-override caveat.
        p = _run_env(["learning", "--disable"], root, "1")
        check("--disable with DEEPMAP_LEARNING=1 exits 0", p.returncode == 0, p.stderr.strip())
        check("--disable warns the truthy env still forces learning ON",
              "DEEPMAP_LEARNING" in p.stdout and "still forces" in p.stdout, p.stdout[-400:])
        # cross-check the caveat is TRUE: learning really is still enabled under that env.
        st = LL.learning_status(root, env={"DEEPMAP_LEARNING": "1"})
        check("learning_status confirms enabled=True under DEEPMAP_LEARNING=1 (why the caveat matters)",
              st.get("enabled") is True, str(st.get("enabled")))
        # (b) no env var -> the caveat is CONDITIONAL, not printed.
        p2 = _run_env(["learning", "--disable"], root, None)
        check("--disable without the env var prints NO caveat (conditional, not always-on)",
              "still forces" not in p2.stdout, p2.stdout[-300:])


_INTERNAL_ID = re.compile(r"RM-1|\bG4\b")   # internal roadmap / launch-gate codenames: never in what a user reads


def gate_cli_no_internal_ids() -> None:
    """What `deepmap learning` / `deepmap tune` print is user-facing: it names the rule in plain words ("until the
    benchmark shows learned weights beat equal weights"), never an internal codename (RM-1, G4)."""
    print("\n-- learning / tune output: plain words, no internal codenames (RM-1, G4) --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        outs = {}
        for args in (["learning", "--enable"], ["learning", "-h"], ["tune", "-h"], ["tune"], ["tune", "--report"]):
            p = _run(args, root)
            name = " ".join(args)
            outs[name] = (p.stdout or "") + (p.stderr or "")
            check(f"`deepmap {name}` exits 0", p.returncode == 0, outs[name][-300:])
        for name, text in outs.items():
            hits = sorted(set(_INTERNAL_ID.findall(text)))
            check(f"`deepmap {name}` names no internal codename (RM-1, G4)", not hits, str(hits))
        for name in ("learning --enable", "tune"):
            check(f"`deepmap {name}` states the rule in plain words ('equal weights')",
                  "equal weights" in outs[name], outs[name][-400:])
    check("the codename pattern has teeth (self-check)",
          bool(_INTERNAL_ID.search("until gate G4 opens")) and bool(_INTERNAL_ID.search("RM-1 tuner"))
          and not _INTERNAL_ID.search("G44 / XG4 / equal weights"))


def gate_wiring(C) -> None:
    print("\n-- wiring: the subcommand dispatches, and status prints accrual BEFORE the DB connect --")
    p = C.build_parser()
    a = p.parse_args(["learning", "--enable"])
    check("learning -> cmd_learning", a.fn is C.cmd_learning)
    check("learning --enable/--disable/--status flags parse",
          a.enable is True and p.parse_args(["learning", "--disable"]).disable is True
          and p.parse_args(["learning", "--status"]).status is True)
    src = (BUILD / "deepmap_cli.py").read_text(encoding="utf-8")
    i = src.find("def cmd_status(")
    body = src[i: src.find("\ndef ", i + 1)]
    check("cmd_status emits the accrual line BEFORE `drv = _driver()`",
          0 < body.find("accrual_line") < body.find("_driver()"), "")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — RM-1 dogfood enable + accrual-line gates (no Neo4j, no spend)")
    print("=" * 68)
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    LL = _load("learning_loop")
    L = _load("retrieval_log")
    C = _load("deepmap_cli")

    gate_helpers(LL)
    gate_status_and_format(LL, L)
    gate_cli_enable_disable_status()
    gate_disable_env_caveat(LL)
    gate_cli_no_internal_ids()
    gate_wiring(C)

    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
