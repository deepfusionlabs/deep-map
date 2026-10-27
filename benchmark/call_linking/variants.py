#!/usr/bin/env python3
"""The §8.3 variant table as data — the ONE place a variant's flags are spelled (run_matrix.sh, run_variant.py,
the README and PREREG.md all read it; tests/call_linking_harness_gates.py locks it against PREREG §8.3).

Every variant runs with a CLEAN environment: every DEEPMAP_* variable is removed except the run settings
(_common.RUN_SETTINGS: DEEPMAP_HOME, DEEPMAP_PYTHON, DEEPMAP_GRAPHIFY_TIMEOUT), NEO4J_URI is removed (a live run
must route through the throwaway project's own registry entry), then the variant's flags are set. So V0 is
"the default" whatever stray flags the operator's shell holds.

usage:
  python variants.py list                       # id, slug and flags of every variant
  python variants.py env <id> [--set K=V ...]   # the flags as KEY=VAL lines (VRC needs --set)
  python variants.py run <id> [--set K=V ...] -- <command ...>   # run a command under the variant env
  python variants.py slug <id>                  # the file-name-safe spelling of an id
"""
from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import RUN_SETTINGS  # noqa: E402

TYPED = "DEEPMAP_TYPED_CALLS"
AUDIT = "DEEPMAP_CALLS_AUDIT"
PRECISE = "DEEPMAP_PRECISE_CALLS"
WAVE1 = "python,typescript,javascript"
SHAPES = ("R1", "R2", "R3", "R4", "S", "K", "M", "C")

_VT = {TYPED: WAVE1, AUDIT: "1"}
_VP = {PRECISE: "python,js"}

VARIANTS = {
    "V0": {},
    "VF": {"DEEPMAP_CALLS_FAMILY_FILTER": "1"},
    "VA": {AUDIT: "1"},
    "VT": dict(_VT),
    "VT-a": {TYPED: WAVE1, AUDIT: "0"},
    "VT+t": dict(_VT, DEEPMAP_TYPED_CALLS_TESTS="1"),
    "VT+np": dict(_VT, DEEPMAP_TYPED_CALLS_NONPROD="1"),
    **{f"VT[{s}]": dict(_VT, DEEPMAP_TYPED_CALLS_SHAPES=s) for s in SHAPES},
    "VP": dict(_VP),
    "VPA": dict(_VP, **{AUDIT: "1"}),
    "VPT": dict(_VP, **_VT),
    "V9": dict(_VP, **_VT, DEEPMAP_GRAPHIFY_CALLS_PLUS="1"),
    # VRC = the exact combination DECISION.md flips (confirmatory). It has no fixed flags: pass them with --set.
    "VRC": None,
}


def slug(vid: str) -> str:
    """A file-name-safe spelling: VT-a -> VT-a, VT+t -> VTpt, VT+np -> VTpnp, VT[R1] -> VT_R1."""
    return vid.replace("+", "p").replace("[", "_").replace("]", "")


def resolve(vid: str) -> str:
    for k in VARIANTS:
        if vid == k or vid == slug(k):
            return k
    raise KeyError(f"unknown variant {vid!r} (known: {', '.join(VARIANTS)})")


def parse_sets(items) -> dict:
    out = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError(f"--set expects KEY=VAL (got {item!r})")
        k, v = item.split("=", 1)
        if not k.startswith("DEEPMAP_") or k in RUN_SETTINGS:
            raise ValueError(f"--set takes graph flags only (a DEEPMAP_* key other than {', '.join(RUN_SETTINGS)}): {k!r}")
        out[k] = v
    return out


def flags_of(vid: str, sets=None) -> dict:
    vid = resolve(vid)
    base = VARIANTS[vid]
    extra = parse_sets(sets)
    if base is None:
        if not extra:
            raise ValueError("VRC has no fixed flags: give the DECISION.md combination with --set KEY=VAL ...")
        return dict(sorted(extra.items()))
    return dict(sorted({**base, **extra}.items()))


def child_env(vid: str, base=None, sets=None) -> dict:
    """os.environ (or `base`) minus every DEEPMAP_* except RUN_SETTINGS and minus NEO4J_URI, plus the variant's
    flags, PYTHONUTF8=1 and PYTHONHASHSEED=0 (design §8.2)."""
    e = dict(os.environ if base is None else base)
    for k in list(e):
        if k == "NEO4J_URI" or (k.startswith("DEEPMAP_") and k not in RUN_SETTINGS):
            e.pop(k)
    e.update(flags_of(vid, sets))
    e["PYTHONUTF8"] = "1"
    e["PYTHONHASHSEED"] = "0"
    return e


def _split(argv):
    sets, rest, cmd = [], [], []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--":
            cmd = argv[i + 1:]
            break
        if a == "--set" and i + 1 < len(argv):
            sets.append(argv[i + 1])
            i += 2
            continue
        rest.append(a)
        i += 1
    return rest, sets, cmd


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    rest, sets, cmd = _split(argv)
    if not rest or rest[0] not in ("list", "env", "run", "slug"):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        if rest[0] == "slug" and len(rest) == 2:
            print(slug(resolve(rest[1])))
            return 0
        if rest[0] == "list":
            for vid, fl in VARIANTS.items():
                spelled = "(set at DECISION time with --set)" if fl is None else (
                    " ".join(f"{k}={v}" for k, v in sorted(fl.items())) or "(the default)")
                print(f"{vid:8s} {slug(vid):8s} {spelled}")
            return 0
        if len(rest) < 2:
            print("give a variant id", file=sys.stderr)
            return 2
        if rest[0] == "env":
            for k, v in flags_of(rest[1], sets).items():
                print(f"{k}={v}")
            return 0
        if not cmd:
            print("run: give the command after --", file=sys.stderr)
            return 2
        return subprocess.call(cmd, env=child_env(rest[1], sets=sets))
    except (KeyError, ValueError) as exc:
        print(f"[variants] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
