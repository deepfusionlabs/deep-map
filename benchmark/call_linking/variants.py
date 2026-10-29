#!/usr/bin/env python3
"""The §8.3 variant table as data — the ONE place a variant's flags are spelled (run_matrix.sh, run_variant.py,
the README and PREREG.md all read it; tests/call_linking_harness_gates.py locks it against PREREG §8.3).

Every variant runs with a CLEAN environment: every DEEPMAP_* variable is removed except the run settings
(_common.RUN_SETTINGS: DEEPMAP_HOME, DEEPMAP_PYTHON, DEEPMAP_GRAPHIFY_TIMEOUT), NEO4J_URI is removed (a live run
must route through the throwaway project's own registry entry), then the variant's flags are set. So V0 is
"the default" whatever stray flags the operator's shell holds.

The D3-cs variants (benchmark/call_linking/PREREG_CS.md §8.4) are a SECOND table, CS_VARIANTS, apart from §8.3's
VARIANTS (harness gate H locks VARIANTS to PREREG.md §8.3, and run_matrix.sh's PREREG matrix runs `list`): V0,
VT-cs, VT-cs[shape] (one per shape), VT-cs+t, VT-cs+np, VT-cs-ifs (DEEPMAP_TYPED_CALLS_IFACE=static: the statically
bound member only, interface or virtual — AC9: the product's switch governs interface AND virtual dispatch),
VT-cs-if0 (DEEPMAP_TYPED_CALLS_IFACE=0: neither dispatch rule) and VRC-cs (set with --set). `env`, `run` and `slug`
take an id of either table; `list` prints §8.3's table only, `list-cs` the C# one.

VRC-cs (PREREG_CS §8.10 as AC16 fixes it) is `--set DEEPMAP_TYPED_CALLS=<every language in the RC tree's
_TYPED_DEFAULT_LANGS>,csharp`: an explicit DEEPMAP_TYPED_CALLS REPLACES the product's default set
(typed_calls._lang_set), so `csharp` alone would switch every default language off on a tree whose defaults are not
empty. `vrc-cs` prints that flag, read from the RC tree's build/typed_calls.py (_common.BUILD: this kit's repository's
build/, or CALL_LINKING_BUILD); `env` / `run VRC-cs` refuse a DEEPMAP_TYPED_CALLS that leaves out csharp or a default
language (more --set flags are allowed: a release that flips another registered decision too runs their union).

usage:
  python variants.py list                       # id, slug and flags of every §8.3 variant
  python variants.py list-cs                    # id, slug and flags of every D3-cs variant (PREREG_CS §8.4)
  python variants.py env <id> [--set K=V ...]   # the flags as KEY=VAL lines (VRC / VRC-cs need --set)
  python variants.py run <id> [--set K=V ...] -- <command ...>   # run a command under the variant env
  python variants.py slug <id>                  # the file-name-safe spelling of an id
  python variants.py vrc-cs                     # VRC-cs's DEEPMAP_TYPED_CALLS=<RC defaults>,csharp (AC16), for --set
"""
from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import BUILD, RUN_SETTINGS, typed_default_langs  # noqa: E402

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

# D3-cs (PREREG_CS §8.4), one table apart from §8.3's, in the registered order. VT-cs sets no audit flag (the arm is
# audit-free, as VT-a was). CS_SHAPES spells typed_calls.SHAPES as PREREG_CS registers them (R1 R2 R3 R4 S K M C).
CSHARP = "csharp"
IFACE = "DEEPMAP_TYPED_CALLS_IFACE"
CS_SHAPES = ("R1", "R2", "R3", "R4", "S", "K", "M", "C")
_VTCS = {TYPED: CSHARP}
CS_VARIANTS = {
    "V0": {},
    "VT-cs": dict(_VTCS),
    **{f"VT-cs[{s}]": dict(_VTCS, DEEPMAP_TYPED_CALLS_SHAPES=s) for s in CS_SHAPES},
    "VT-cs+t": dict(_VTCS, DEEPMAP_TYPED_CALLS_TESTS="1"),
    "VT-cs+np": dict(_VTCS, DEEPMAP_TYPED_CALLS_NONPROD="1"),
    # AC9: DEEPMAP_TYPED_CALLS_IFACE governs interface AND virtual dispatch — static keeps only the statically bound
    # member (interface or virtual), 0 neither rule; delta(VT-cs-ifs, VT-cs)'s added bucket is the dispatch stratum
    # (the implementer and override edges, G3b)
    "VT-cs-ifs": dict(_VTCS, **{IFACE: "static"}),
    "VT-cs-if0": dict(_VTCS, **{IFACE: "0"}),
    # VRC-cs = the exact flip combination, set at decision time (DECISION_CS.md) with --set; for this decision
    # `--set DEEPMAP_TYPED_CALLS=<every language in the RC tree's _TYPED_DEFAULT_LANGS>,csharp` (PREREG_CS §8.10,
    # AC16: an explicit value replaces the default set; today the defaults are empty, so it is `csharp`): vrc_cs_flag
    "VRC-cs": None,
}
# The arms that need the RC tree to read DEEPMAP_TYPED_CALLS_IFACE (PREREG_CS §8.4: not run without it).
CS_IFACE_ARMS = ("VT-cs-ifs", "VT-cs-if0")
_CONFIRMATORY = {"VRC": "DECISION.md", "VRC-cs": "DECISION_CS.md"}


def slug(vid: str) -> str:
    """A file-name-safe spelling: VT-a -> VT-a, VT+t -> VTpt, VT+np -> VTpnp, VT[R1] -> VT_R1 (VT-cs+t -> VT-cspt,
    VT-cs[R1] -> VT-cs_R1)."""
    return vid.replace("+", "p").replace("[", "_").replace("]", "")


def _table(vid: str) -> dict:
    """The table that defines the (resolved) id: §8.3's VARIANTS first, then CS_VARIANTS (V0 is in both, {})."""
    return VARIANTS if vid in VARIANTS else CS_VARIANTS


def resolve(vid: str) -> str:
    for table in (VARIANTS, CS_VARIANTS):
        for k in table:
            if vid == k or vid == slug(k):
                return k
    raise KeyError(f"unknown variant {vid!r} (known: {', '.join(VARIANTS)}; D3-cs: "
                   f"{', '.join(k for k in CS_VARIANTS if k not in VARIANTS)})")


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


def vrc_cs_flag(build_dir=None) -> str:
    """AC16: the value of VRC-cs's DEEPMAP_TYPED_CALLS — every language in the RC tree's _TYPED_DEFAULT_LANGS (sorted),
    then csharp (`csharp` alone while the defaults are empty). Read from build_dir (default _common.BUILD)."""
    langs = [x for x in typed_default_langs(build_dir or BUILD) if x != CSHARP]
    return ",".join(langs + [CSHARP])


def vrc_cs_problem(extra: dict, build_dir=None) -> "str | None":
    """Why VRC-cs's --set flags are not AC16's flip combination, or None: DEEPMAP_TYPED_CALLS must be given and name
    csharp and every language of the RC tree's _TYPED_DEFAULT_LANGS (an explicit value replaces the defaults; an
    ON-word means wave 1, which has no csharp). Languages beyond those are allowed (a release flipping another
    registered decision too runs the union)."""
    value = extra.get(TYPED)
    try:
        defaults = typed_default_langs(build_dir or BUILD)
    except Exception as exc:  # noqa: BLE001 — the defaults cannot be read: the combination cannot be shown (fail closed)
        return f"the RC tree's typed_calls._TYPED_DEFAULT_LANGS cannot be read ({type(exc).__name__}: {exc})"
    want = sorted(set(defaults) | {CSHARP})
    if value is None:
        return f"VRC-cs needs --set {TYPED}={vrc_cs_flag(build_dir)} (AC16: every default language plus csharp)"
    raw = str(value).strip().lower()
    got = set() if raw in ("", "0", "false", "no", "off", "1", "true", "yes", "on") else {
        x.strip() for x in raw.split(",") if x.strip()}
    missing = [x for x in want if x not in got]
    if missing:
        return (f"{TYPED}={value} leaves out {', '.join(missing)}: an explicit value REPLACES the RC tree's default set "
                f"{defaults}, so VRC-cs is --set {TYPED}={vrc_cs_flag(build_dir)} (AC16)")
    return None


def flags_of(vid: str, sets=None) -> dict:
    vid = resolve(vid)
    base = _table(vid)[vid]
    extra = parse_sets(sets)
    if base is None:
        if not extra:
            raise ValueError(f"{vid} has no fixed flags: give the {_CONFIRMATORY.get(vid, 'DECISION.md')} combination "
                             f"with --set KEY=VAL ...")
        if vid == "VRC-cs":
            why = vrc_cs_problem(extra)
            if why:
                raise ValueError(why)
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
    if not rest or rest[0] not in ("list", "list-cs", "env", "run", "slug", "vrc-cs"):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        if rest[0] == "vrc-cs":
            print(f"{TYPED}={vrc_cs_flag()}")
            return 0
        if rest[0] == "slug" and len(rest) == 2:
            print(slug(resolve(rest[1])))
            return 0
        if rest[0] == "list":
            for vid, fl in VARIANTS.items():
                spelled = "(set at DECISION time with --set)" if fl is None else (
                    " ".join(f"{k}={v}" for k, v in sorted(fl.items())) or "(the default)")
                print(f"{vid:8s} {slug(vid):8s} {spelled}")
            return 0
        if rest[0] == "list-cs":
            for vid, fl in CS_VARIANTS.items():
                spelled = "(set at DECISION_CS time with --set)" if fl is None else (
                    " ".join(f"{k}={v}" for k, v in sorted(fl.items())) or "(the default)")
                print(f"{vid:10s} {slug(vid):10s} {spelled}")
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
    except (KeyError, ValueError, ImportError, OSError, AttributeError) as exc:
        print(f"[variants] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
