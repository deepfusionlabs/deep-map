#!/usr/bin/env python3
"""auto-sync on macOS — build/auto_sync._memory reads the available memory a Mac reports (vm_stat), so the background
refresh's low-memory floor (R13) and its pre-embed wait (_before_embed) fire on a Mac as they do on Windows and Linux.
Before: _memory read /proc/meminfo outside Windows; macOS has no /proc, so `available` was always None there and
neither the floor nor the wait ever fired on a Mac.

  A. the darwin dispatch through the REAL no-argument _memory() (what R13 and _before_embed call), with auto_sync's
     `sys` / `os` seen as a Mac's (sys.platform "darwin", os.name "posix", sysconf -> the machine's RAM) and vm_stat's
     output fed through the _vm_stat_text seam: available = (free + inactive + speculative pages) x the page size the
     header names — never sysconf's page size (a process Rosetta translates is told 4 KB on a 16 KB Mac); the
     total stays sysconf's, else sysctl hw.memsize (_sysctl_memsize); /proc is never read on a Mac.
  B. the parser (_vm_stat_available) on outputs in vm_stat's layout (the header naming the page size, one aligned
     row per counter ending in a period) for macOS 14 / 15 / 26 on Apple silicon (16 KB pages) and macOS 14 on Intel
     (4 KB pages); tolerant of an added row, reordered rows, CRLF, tabs, a missing period; "Pages purgeable" never
     counts; near-miss labels ("Pages freed", "Pages reactivated") never count; None — never a crash, never a guess —
     for every malformed shape (no header, a bad page size, a missing / duplicated / non-integer row, the columnar
     `vm_stat <interval>` table, more available than the machine has, ...); a seeded fuzz never raises.
  C. the vm_stat runner (_vm_stat_text): /usr/bin/vm_stat, about 5 s at most (a child that hangs is ended at the
     timeout), a non-zero exit or a missing program -> None, never raises. The DEFAULT call (no arguments: the one
     _darwin_memory makes, so R13's and _before_embed's), seen through a recorder in place of subprocess.run: exactly
     [/usr/bin/vm_stat], no shell, timeout VM_STAT_TIMEOUT_S as it reads when vm_stat runs (5 s); and, behaviourally,
     with VM_STAT_TIMEOUT_S set small, a call with no timeout argument on a sleeping child — and _memory() on a Mac
     whose vm_stat sleeps — return None at that bound, well before the child would end.
  D. the runner on a Mac: under memory pressure R13 skips (skipped:low-memory, the reason names the reading) and the
     pre-embed wait polls for 2 minutes then postpones the embeddings; memory that recovers mid-wait embeds; a Mac
     with room runs.
  E. shapes that must NOT change: on Windows and Linux _memory never runs vm_stat; Linux still reads /proc/meminfo
     (MemAvailable + MemTotal; no MemAvailable -> available None, never vm_stat; no /proc -> sysconf's total); the
     real platform's own reading.

The vm_stat texts are written in the layout Apple's vm_stat prints (labels and order as in its source); the counts are
illustrative for the machine named, not captures from a Mac — the macOS workflow (tests/live_macos_e2e.py,
"auto_sync knows the available memory") checks the real program on real runners.

Hermetic: stdlib only; no Neo4j, no network; a temp dir (the runner's root, the child's input file), removed by its
exact path; the only subprocesses are short `python -c` children this gate starts.
Usage: python tests/auto_sync_macos_memory_gates.py
"""
from __future__ import annotations

import builtins
import importlib.util
import io
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

sys.dont_write_bytecode = True

REPO = Path(__file__).resolve().parent.parent
PASSES: list = []
FAILS: list = []
SKIPS: list = []
GIB = 1024 ** 3

# vm_stat's rows, in the order it prints them
ROWS = ("Pages free", "Pages active", "Pages inactive", "Pages speculative", "Pages throttled", "Pages wired down",
        "Pages purgeable", '"Translation faults"', "Pages copy-on-write", "Pages zero filled", "Pages reactivated",
        "Pages purged", "File-backed pages", "Anonymous pages", "Pages stored in compressor",
        "Pages occupied by compressor", "Decompressions", "Compressions", "Pageins", "Pageouts", "Swapins", "Swapouts")


def vm_text(page: int, values, eol: str = "\n") -> str:
    """vm_stat's snapshot layout: the header, then each label padded to 30 columns and the count right-aligned in 16,
    ending in a period."""
    lines = [f"Mach Virtual Memory Statistics: (page size of {page} bytes)"]
    lines += [f"{label + ':':<30} {int(v):>16}." for label, v in zip(ROWS, values)]
    return eol.join(lines) + eol


# macOS 14 Sonoma, Apple M1, 16 GB (written out literally, as vm_stat prints it)
M1_14_16GB = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               13105.
Pages active:                            381874.
Pages inactive:                          372233.
Pages speculative:                         5925.
Pages throttled:                              0.
Pages wired down:                        150422.
Pages purgeable:                           9133.
"Translation faults":                1093288441.
Pages copy-on-write:                   41933011.
Pages zero filled:                    538229110.
Pages reactivated:                     10238847.
Pages purged:                           2210311.
File-backed pages:                       254017.
Anonymous pages:                         505915.
Pages stored in compressor:              412776.
Pages occupied by compressor:            112390.
Decompressions:                        20183374.
Compressions:                          26662810.
Pageins:                               21911047.
Pageouts:                                251998.
Swapins:                                      0.
Swapouts:                                     0.
"""
M1_14_TOTAL = 16 * GIB
M1_14_AVAIL = 6410452992                      # (13105 + 372233 + 5925) x 16384, worked by hand

# name -> (page size, the 22 counts, the machine's RAM); free / inactive / speculative are values[0] / [2] / [3]
MACS = {
    "macOS 15, Apple M2 Pro, 32 GB, 16 KB pages": (16384, [
        52341, 701233, 689402, 12877, 0, 298551, 18734, 2873301922, 98213004, 1310228711, 31220954, 8810233, 611208,
        843304, 1209877, 301220, 61233098, 80112904, 50211873, 1022871, 112004, 301233], 32 * GIB),
    "macOS 26, Apple M4, 24 GB, 16 KB pages": (16384, [
        21877, 563201, 541093, 9312, 0, 231877, 12045, 402118733, 15233871, 201877342, 3022194, 410227, 402331, 711275,
        502113, 190334, 5233109, 7120944, 6229011, 40112, 0, 0], 24 * GIB),
    "macOS 14, Intel Core i7, 16 GB, 4 KB pages": (4096, [
        81233, 1412556, 1388012, 41882, 0, 701334, 66021, 1822119034, 77120933, 1022877410, 20118774, 3302211, 1211873,
        1630577, 1902231, 512447, 41233988, 52119873, 38110223, 2201877, 410223, 822104], 16 * GIB),
}
WANT = {"macOS 15, Apple M2 Pro, 32 GB, 16 KB pages": 12363694080,     # 754620 x 16384
        "macOS 26, Apple M4, 24 GB, 16 KB pages": 9376268288,          # 572282 x 16384
        "macOS 14, Intel Core i7, 16 GB, 4 KB pages": 6189576192}      # 1511127 x 4096

# an 8 GB Apple-silicon Mac (macOS 15) under pressure, and the same Mac with room again
M1_8GB = 8 * GIB
PRESSURE_8GB = vm_text(16384, [3712, 180311, 98113, 1830, 0, 121004, 2210, 901233871, 30221877, 401229933, 22110933,
                               1022871, 61233, 220021, 702331, 112877, 61022331, 70331029, 30221877, 1203377, 2201338,
                               2903117])
PRESSURE_AVAIL = 1698283520                   # 103655 x 16384 (1.6 GB) < the 3 GiB floor
ROOM_8GB = vm_text(16384, [120000, 100000, 110000, 5000, 0, 60000, 3000, 901233871, 30221877, 401229933, 22110933,
                           1022871, 61233, 220021, 702331, 20000, 61022331, 70331029, 30221877, 1203377, 2201338,
                           2903117])
ROOM_AVAIL = 3850240000                       # 235000 x 16384 (3.6 GB) >= the 3 GiB floor

MEMINFO = """MemTotal:       16303428 kB
MemFree:         1203348 kB
MemAvailable:    9822104 kB
Buffers:          402112 kB
Cached:          7712093 kB
SwapTotal:       2097148 kB
"""


def check(name: str, ok: bool, detail="") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {str(detail)[:900]}"))


def skip(name: str, why: str) -> None:
    SKIPS.append(name)
    print(f"  [SKIP] {name} — {why}")


def _load():
    build = str(REPO / "build")
    if build not in sys.path:
        sys.path.insert(0, build)
    spec = importlib.util.spec_from_file_location(f"auto_sync_macmem_{time.time_ns()}", str(REPO / "build" /
                                                                                          "auto_sync.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Proxy:
    """A module seen with some attributes replaced; every other attribute is the real module's."""

    def __init__(self, real, **over):
        self._real = real
        self._over = over

    def __getattr__(self, name):
        over = self.__dict__["_over"]
        return over[name] if name in over else getattr(self.__dict__["_real"], name)


class _Platform:
    """auto_sync seen on another OS: its `sys` / `os` globals proxied (sys.platform, os.name, os.sysconf), its `open`
    recording every path (a fake /proc/meminfo, else the real open), and vm_stat / sysctl fed through their seams
    (`real_vm_stat=True` keeps the real _vm_stat_text, for a recorder in place of subprocess.run: _RunRecorder).
    Every replaced attribute is put back on exit."""

    def __init__(self, A, platform: str, *, osname="posix", sysconf=None, vm_stat=None, memsize=None, meminfo=None,
                 real_vm_stat=False):
        self.A, self.platform, self.osname = A, platform, osname
        self.sysconf, self.vm_stat, self.memsize, self.meminfo = sysconf, vm_stat, memsize, meminfo
        self.real_vm_stat = real_vm_stat
        self.opened: list = []
        self.vm_calls = 0
        self.saved: dict = {}

    def _open(self, path, *a, **k):
        self.opened.append(str(path))
        if str(path) == "/proc/meminfo":
            if self.meminfo is None:
                raise FileNotFoundError(2, "No such file or directory", str(path))
            return io.StringIO(self.meminfo)
        return builtins.open(path, *a, **k)

    def _vm(self, *a, **k):
        self.vm_calls += 1
        v = self.vm_stat
        if callable(v):
            return v()
        return v

    def __enter__(self):
        A = self.A
        over_os = {"name": self.osname}
        if self.sysconf is not None:
            over_os["sysconf"] = self.sysconf
        elif hasattr(os, "sysconf"):
            def _no_sysconf(name):
                raise ValueError(f"unrecognized configuration name {name!r}")
            over_os["sysconf"] = _no_sysconf
        for name, value in (("sys", _Proxy(sys, platform=self.platform)), ("os", _Proxy(os, **over_os)),
                            ("open", self._open), ("_vm_stat_text", self._vm),
                            ("_sysctl_memsize", (lambda: self.memsize))):
            if name == "_vm_stat_text" and self.real_vm_stat:
                continue
            self.saved[name] = A.__dict__.get(name, _MISSING)
            setattr(A, name, value)
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            if value is _MISSING:
                self.A.__dict__.pop(name, None)
            else:
                setattr(self.A, name, value)
        return False


_MISSING = object()


def sysconf_for(total: int, page: int):
    """os.sysconf answering SC_PHYS_PAGES / SC_PAGE_SIZE for a machine with `total` bytes in `page`-byte pages."""
    table = {"SC_PHYS_PAGES": total // page, "SC_PAGE_SIZE": page}

    def _sysconf(name):
        if name not in table:
            raise ValueError(f"unrecognized configuration name {name!r}")
        return table[name]
    return _sysconf


def _sleeper_python() -> str:
    """The interpreter for the gate's sleeping `python -c` children: the base interpreter, never a Windows venv
    launcher (it starts the real interpreter as a grandchild, which outlives the timeout's kill and holds the output
    pipe open, so the run would wait the whole sleep)."""
    base = getattr(sys, "_base_executable", None)
    return base if isinstance(base, str) and base and os.path.isfile(base) else sys.executable


_REAL_RUN = subprocess.run
SLEEP_S = 30                     # how long a sleeping child sleeps: far past any bound the checks allow
SLEEPER = [_sleeper_python(), "-c", f"import time; time.sleep({SLEEP_S})"]


class _RunRecorder:
    """subprocess.run replaced by a recorder, the call _vm_stat_text makes (it imports subprocess when it runs, so the
    module's `run` is what it reaches). Each call is recorded as (argv, keyword arguments) and answered by
    `answer(real_run, argv, kw)`. The real subprocess.run is put back on exit."""

    def __init__(self, answer):
        self.answer = answer
        self.calls: list = []
        self.real = None

    def _run(self, *args, **kw):
        argv = args[0] if args else kw.get("args")
        self.calls.append((argv, dict(kw)))
        return self.answer(self.real, argv, kw)

    def __enter__(self):
        self.real = subprocess.run
        subprocess.run = self._run
        return self

    def __exit__(self, *exc):
        subprocess.run = self.real
        return False


def _canned(real, argv, kw):
    """A recorded vm_stat that exits 0 having printed the macOS 14 Apple M1 16 GB snapshot."""
    return subprocess.CompletedProcess(argv, 0, stdout=M1_14_16GB, stderr=None)


def _swap_for_sleeper(real, argv, kw):
    """The module's own call, run for real with every keyword argument it passed (its timeout among them); only the
    program is swapped for a child that sleeps SLEEP_S."""
    return real(list(SLEEPER), **kw)


def _is_vm_stat_call(call, timeout) -> bool:
    """`call` (argv, kw) runs exactly ["/usr/bin/vm_stat"] (VM_STAT) with timeout == `timeout`, a number."""
    argv, kw = call
    t = kw.get("timeout")
    return (isinstance(argv, (list, tuple)) and list(argv) == ["/usr/bin/vm_stat"]
            and isinstance(t, (int, float)) and not isinstance(t, bool) and t == timeout)


def _section(title: str, fn, *args) -> None:
    print(f"\n-- {title} --")
    try:
        fn(*args)
    except Exception as exc:  # noqa: BLE001
        check(f"{title}: the section ran to the end", False, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")


# =====================================================================================================================
# A — the darwin dispatch through the real _memory()
# =====================================================================================================================
def gate_dispatch(A) -> None:
    with _Platform(A, "darwin", sysconf=sysconf_for(M1_14_TOTAL, 16384), vm_stat=M1_14_16GB) as P:
        got = A._memory()
    check("on a Mac, _memory() reads the available memory from vm_stat: (free + inactive + speculative) x 16384 "
          "on an Apple-silicon Mac, the total from sysconf", got == (M1_14_AVAIL, M1_14_TOTAL), got)
    check("... it runs vm_stat once and never opens /proc/meminfo (macOS has no /proc)",
          P.vm_calls == 1 and not any(p.startswith("/proc") for p in P.opened), (P.vm_calls, P.opened))

    for name, (page, values, total) in MACS.items():
        with _Platform(A, "darwin", sysconf=sysconf_for(total, page), vm_stat=vm_text(page, values)):
            got = A._memory()
        check(f"on a Mac ({name}), _memory() == (available, total)", got == (WANT[name], total), (got, WANT[name]))

    # Rosetta: a translated process's sysconf says 4 KB pages; vm_stat's own header (16 KB) is what its counts are in
    with _Platform(A, "darwin", sysconf=sysconf_for(M1_14_TOTAL, 4096), vm_stat=M1_14_16GB):
        got = A._memory()
    check("the page size is the one vm_stat's header names, never sysconf's (a process Rosetta translates is told "
          "4 KB pages on a 16 KB Mac)", got == (M1_14_AVAIL, M1_14_TOTAL), got)

    with _Platform(A, "darwin", sysconf=None, vm_stat=M1_14_16GB, memsize=M1_14_TOTAL):
        got = A._memory()
    check("sysconf unavailable on the Mac -> the total comes from sysctl hw.memsize (_sysctl_memsize)",
          got == (M1_14_AVAIL, M1_14_TOTAL), got)
    with _Platform(A, "darwin", sysconf=None, vm_stat=M1_14_16GB, memsize=None):
        got = A._memory()
    check("neither sysconf nor hw.memsize answers -> total None, the available memory still read (the floor is "
          "then MEM_FLOOR_MIN)", got == (M1_14_AVAIL, None), got)

    for label, feed in (("vm_stat missing or failed (None)", None), ("vm_stat printed nothing", ""),
                        ("vm_stat printed garbage", "\x00\xff garbage\n")):
        with _Platform(A, "darwin", sysconf=sysconf_for(M1_14_TOTAL, 16384), vm_stat=feed) as P:
            got = A._memory()
        check(f"{label} -> (None, total): no available memory guessed, the total kept",
              got == (None, M1_14_TOTAL) and P.vm_calls == 1, (got, P.vm_calls))

    def boom():
        raise RuntimeError("vm_stat seam exploded")
    with _Platform(A, "darwin", sysconf=sysconf_for(M1_14_TOTAL, 16384), vm_stat=boom) as P:
        try:
            got = A._memory()
            ok = got == (None, M1_14_TOTAL) and P.vm_calls == 1
        except Exception as exc:  # noqa: BLE001
            got, ok = f"raised {type(exc).__name__}: {exc}", False
    check("a vm_stat reader that raises -> (None, total); _memory never raises", ok, got)

    page, values, _ = MACS["macOS 15, Apple M2 Pro, 32 GB, 16 KB pages"]
    with _Platform(A, "darwin", sysconf=sysconf_for(M1_8GB, 16384), vm_stat=vm_text(page, values)) as P:
        got = A._memory()
    check("vm_stat saying more is available than the machine has (12.4 GB on an 8 GB Mac) -> available None, never "
          "a guess", got == (None, M1_8GB) and P.vm_calls == 1, (got, P.vm_calls))


# =====================================================================================================================
# B — the parser
# =====================================================================================================================
def gate_parser(A) -> None:
    P = getattr(A, "_vm_stat_available", None)
    check("auto_sync has the vm_stat parser _vm_stat_available(text, total)", callable(P), P)
    if not callable(P):
        return
    check("macOS 14, Apple M1 16 GB (16 KB pages): 6410452992 bytes available", P(M1_14_16GB, M1_14_TOTAL) ==
          M1_14_AVAIL, P(M1_14_16GB, M1_14_TOTAL))
    check("... the same without a known total", P(M1_14_16GB) == M1_14_AVAIL, P(M1_14_16GB))
    for name, (page, values, total) in MACS.items():
        txt = vm_text(page, values)
        indep = (values[0] + values[2] + values[3]) * page
        check(f"{name}: free + inactive + speculative pages x page size", P(txt, total) == WANT[name] == indep,
              (P(txt, total), WANT[name], indep))
    page, values, total = MACS["macOS 15, Apple M2 Pro, 32 GB, 16 KB pages"]
    base = P(vm_text(page, values), total)

    four = vm_text(4096, values)
    check("the header's page size scales the counts (the same rows under a 4 KB header read a quarter)",
          P(four, total) == base // 4, (P(four, total), base))
    check("CRLF line ends read the same", P(vm_text(page, values, eol="\r\n"), total) == base)
    lines = vm_text(page, values).splitlines()
    reordered = "\n".join([lines[0]] + list(reversed(lines[1:]))) + "\n"
    check("rows in another order read the same", P(reordered, total) == base, P(reordered, total))
    added = "\n".join(lines[:5] + ["Pages realtime:                                12.",
                                   "Pages in some new queue:                    777777."] + lines[5:]) + "\n"
    check("a row a later macOS adds is ignored (the three rows still read)", P(added, total) == base, P(added, total))
    tabs = vm_text(page, values).replace("Pages free:", "Pages free:\t")
    check("tabs between a label and its count read the same", P(tabs, total) == base, P(tabs, total))
    noperiod = "\n".join(ln.rstrip(".") for ln in lines) + "\n"
    check("counts without vm_stat's trailing period read the same", P(noperiod, total) == base, P(noperiod, total))
    lead = "\n\n" + vm_text(page, values)
    check("blank lines before the header are skipped", P(lead, total) == base, P(lead, total))

    v2 = list(values)
    v2[6] = 999999                                  # Pages purgeable
    check("'Pages purgeable' never counts (a purgeable page also sits on the active / inactive queue: adding it "
          "would count pages twice)", P(vm_text(page, v2), total) == base, P(vm_text(page, v2), total))
    v3 = list(values)
    v3[1], v3[5], v3[15], v3[12] = 1, 2, 3, 4          # active, wired, compressor, file-backed
    check("active, wired, compressor and file-backed pages never count", P(vm_text(page, v3), total) == base)
    freed = vm_text(page, values) + "Pages freed:                              888888.\n"
    check("a near-miss label ('Pages freed') is not 'Pages free'", P(freed, total) == base, P(freed, total))
    no_free = "\n".join(ln for ln in lines if not ln.startswith("Pages free:")) + \
        "\nPages freed:                              888888.\n"
    check("only a near-miss label and no 'Pages free' row -> None", P(no_free, total) is None, P(no_free, total))
    no_inactive = "\n".join(ln for ln in lines if not ln.startswith("Pages inactive:")) + "\n"
    check("'Pages reactivated' is not 'Pages inactive' (no inactive row -> None)", P(no_inactive, total) is None,
          P(no_inactive, total))

    hdr = lines[0]
    body = "\n".join(lines[1:]) + "\n"
    columnar = (hdr + "\n    free   active   specul inactive throttle    wired  prgable   faults     copy    0fill "
                "reactive   purged file-backed anonymous cmprssed cmprssor  dcomprs   comprs  pageins  pageout  "
                "swapins swapouts\n   52341   701233    12877   689402        0   298551    18734 2873301k 98213004 "
                "1310228k 31220954  8810233   611208   843304  1209877   301220 61233098 80112904 50211873  1022871 "
                "  112004   301233\n")
    huge = "9" * 5000
    malformed = {
        "an empty output": "",
        "None": None,
        "bytes, not text": vm_text(page, values).encode("ascii"),
        "a number, not text": 12345,
        "rows without the header": body,
        "a header without a page size": "Mach Virtual Memory Statistics:\n" + body,
        "page size 0": "Mach Virtual Memory Statistics: (page size of 0 bytes)\n" + body,
        "page size 4097 (not a power of two)": "Mach Virtual Memory Statistics: (page size of 4097 bytes)\n" + body,
        "page size 2048 (below any Mac's)": "Mach Virtual Memory Statistics: (page size of 2048 bytes)\n" + body,
        "page size 1 MiB (above any Mac's)": "Mach Virtual Memory Statistics: (page size of 1048576 bytes)\n" + body,
        "a page size of 5000 digits": f"Mach Virtual Memory Statistics: (page size of {huge} bytes)\n" + body,
        "another program's header": "Linux Memory Statistics: (page size of 4096 bytes)\n" + body,
        "a line before the header": "warning: something\n" + vm_text(page, values),
        "no 'Pages free' row": "\n".join(ln for ln in lines if not ln.startswith("Pages free:")) + "\n",
        "no 'Pages inactive' row": no_inactive,
        "no 'Pages speculative' row": "\n".join(ln for ln in lines if not ln.startswith("Pages speculative:")) + "\n",
        "'Pages free' twice": vm_text(page, values) + "Pages free:                                   1.\n",
        "two snapshots back to back": vm_text(page, values) + vm_text(page, values),
        "a decimal count": vm_text(page, values).replace("52341.", "52341.5."),
        "a negative count": vm_text(page, values).replace(" 52341.", "-52341."),
        "a word for a count": vm_text(page, values).replace("52341.", "lots."),
        "a thousands separator": vm_text(page, values).replace("52341.", "52,341."),
        "a count with a unit": vm_text(page, values).replace("52341.", "52341 pages."),
        "an empty count": vm_text(page, values).replace("52341.", ""),
        "a 5000-digit count": vm_text(page, values).replace("52341.", huge + "."),
        "the header and one row (truncated output)": "\n".join(lines[:2]) + "\n",
        "the columnar `vm_stat <interval>` table": columnar,
        "an error message": "vm_stat: (os/kern) failure (5)\n",
        "binary noise": "\x00\x01\x02\xff\xfe" * 50,
    }
    for label, txt in malformed.items():
        try:
            got = P(txt, total)
            ok = got is None
        except Exception as exc:  # noqa: BLE001
            got, ok = f"raised {type(exc).__name__}: {exc}", False
        check(f"malformed vm_stat output ({label}) -> None, no exception", ok, got)
    check("more available than the machine has -> None (an impossible reading is not believed)",
          P(vm_text(page, values), 8 * GIB) is None, P(vm_text(page, values), 8 * GIB))
    check("a total that is not a positive number is not used for the check (the reading stands)",
          P(vm_text(page, values), 0) == base and P(vm_text(page, values), "x") == base and
          P(vm_text(page, values), True) == base, (P(vm_text(page, values), 0), P(vm_text(page, values), "x")))
    zero = list(values)
    zero[0] = zero[2] = zero[3] = 0
    check("all three queues empty -> 0 (a real reading: the floor fires), not None", P(vm_text(page, zero), total) == 0,
          P(vm_text(page, zero), total))

    rnd = random.Random(20261026)
    seeds = [M1_14_16GB, vm_text(page, values), vm_text(4096, MACS["macOS 14, Intel Core i7, 16 GB, 4 KB pages"][1]),
             PRESSURE_8GB]
    alphabet = "0123456789.: \t\n\r\"-()abcdefPagesfreinactvspul\x00\xff"
    raised, bad = [], []
    for i in range(3000):
        s = list(rnd.choice(seeds))
        for _ in range(rnd.randint(1, 6)):
            op = rnd.randrange(5)
            pos = rnd.randrange(len(s) + 1)
            if op == 0 and s:
                del s[min(pos, len(s) - 1)]
            elif op == 1:
                s.insert(pos, rnd.choice(alphabet))
            elif op == 2:
                s = s[:pos]
            elif op == 3 and s:
                j = rnd.randrange(len(s))
                s[j] = rnd.choice("0123456789")
            else:
                ln = "".join(s).splitlines(True)
                if ln:
                    k = rnd.randrange(len(ln))
                    ln.insert(k, ln[k])
                    s = list("".join(ln))
        txt = "".join(s)
        tot = rnd.choice([None, 32 * GIB, 8 * GIB])
        try:
            r = P(txt, tot)
        except Exception as exc:  # noqa: BLE001
            raised.append(f"{type(exc).__name__}: {exc}")
            continue
        if r is not None and not (isinstance(r, int) and not isinstance(r, bool) and r >= 0
                                  and (tot is None or r <= tot)):
            bad.append(r)
    check("a seeded fuzz of 3000 mutated vm_stat outputs never raises", not raised, raised[:3])
    check("... and every answer is None or a whole number of bytes in [0, the total]", not bad, bad[:3])


# =====================================================================================================================
# C — the vm_stat runner
# =====================================================================================================================
def gate_runner_child(A, tmp: Path) -> None:
    T = getattr(A, "_vm_stat_text", None)
    check("auto_sync has the vm_stat runner _vm_stat_text(argv=None, timeout=None)", callable(T), T)
    if not callable(T):
        return
    check("it runs /usr/bin/vm_stat (an absolute path: never a vm_stat found on PATH) with a 5 s timeout",
          getattr(A, "VM_STAT", None) == "/usr/bin/vm_stat" and getattr(A, "VM_STAT_TIMEOUT_S", None) == 5.0,
          (getattr(A, "VM_STAT", None), getattr(A, "VM_STAT_TIMEOUT_S", None)))
    src = tmp / "vm_stat_out.txt"
    src.write_bytes(M1_14_16GB.encode("ascii"))
    printer = [sys.executable, "-c", "import sys; sys.stdout.write(open(sys.argv[1], encoding='ascii').read())",
               str(src)]
    out = T(printer)
    check("a program that prints vm_stat's output and exits 0 -> its text (which parses)",
          isinstance(out, str) and A._vm_stat_available(out, M1_14_TOTAL) == M1_14_AVAIL, repr(out)[:200])
    failing = [sys.executable, "-c",
               "import sys; sys.stdout.write(open(sys.argv[1], encoding='ascii').read()); sys.exit(1)", str(src)]
    check("a non-zero exit -> None, whatever it printed", T(failing) is None, repr(T(failing))[:200])
    check("a program that does not exist -> None, no exception", T([str(tmp / "no-such-vm_stat")]) is None)
    t0 = time.monotonic()
    hung = T(list(SLEEPER), timeout=1.0)
    took = time.monotonic() - t0
    check("a vm_stat that hangs is ended at the timeout -> None (bounded, about the timeout)",
          hung is None and took < 12.0, (hung, round(took, 2)))
    if os.path.exists("/usr/bin/vm_stat"):
        out = T()
        check("this machine has /usr/bin/vm_stat: _vm_stat_text() returns text that parses",
              isinstance(out, str) and isinstance(A._vm_stat_available(out), int), repr(out)[:300])
    else:
        check("this machine has no /usr/bin/vm_stat: _vm_stat_text() -> None, no exception", T() is None)
    M = getattr(A, "_sysctl_memsize", None)
    D = getattr(A, "_darwin_total", None)
    if callable(M) and callable(D):
        try:
            m, d = M(), D()
            ok = (m is None or (isinstance(m, int) and m > 0)) and (d is None or (isinstance(d, int) and d > 0))
            if sys.platform != "darwin":
                ok = ok and m is None
        except Exception as exc:  # noqa: BLE001
            m, d, ok = f"raised {type(exc).__name__}", None, False
        check("_sysctl_memsize / _darwin_total never raise (hw.memsize is None off a Mac)", ok, (m, d))
    else:
        check("auto_sync has _sysctl_memsize and _darwin_total", False, (M, D))


def gate_default_call(A) -> None:
    """The call _darwin_memory makes (no arguments): the program and the bound it really runs with, not the constants
    alone."""
    T, DM = getattr(A, "_vm_stat_text", None), getattr(A, "_darwin_memory", None)
    if not (callable(T) and callable(DM)):
        check("auto_sync has _vm_stat_text and _darwin_memory", False, (T, DM))
        return
    default = getattr(A, "VM_STAT_TIMEOUT_S", None)

    # A vm_stat planted first on PATH: a PATH lookup would find it (positive control), the module must not.
    plant_dir = Path(tempfile.mkdtemp(prefix="dm_macmem_path_"))
    plant = plant_dir / ("vm_stat.bat" if os.name == "nt" else "vm_stat")
    plant.write_text("@echo planted\n" if os.name == "nt" else "#!/bin/sh\necho planted\n", encoding="ascii")
    if os.name != "nt":
        plant.chmod(0o755)
    saved_path = os.environ.get("PATH", "")
    try:
        os.environ["PATH"] = str(plant_dir) + os.pathsep + saved_path
        found = shutil.which("vm_stat")
        check("positive control: with the planted vm_stat first on PATH, a PATH lookup finds it",
              bool(found) and Path(found).parent == plant_dir, found)
        with _RunRecorder(_canned) as rec:
            out = T()
    finally:
        os.environ["PATH"] = saved_path
        shutil.rmtree(plant_dir, ignore_errors=True)
    check("a call with no arguments (the one _darwin_memory makes) runs exactly [/usr/bin/vm_stat] — never a vm_stat "
          "found on PATH (one is planted first on PATH here) — with timeout VM_STAT_TIMEOUT_S (5 s), and returns "
          "what it printed",
          len(rec.calls) == 1 and _is_vm_stat_call(rec.calls[0], 5.0) and default == 5.0 and out == M1_14_16GB,
          (rec.calls, default, repr(out)[:80]))
    kw = rec.calls[0][1] if rec.calls else {}
    check("... with no shell, stdin from DEVNULL (never the hook's), its output captured and decoded as text",
          not kw.get("shell") and kw.get("stdin") == subprocess.DEVNULL and kw.get("stdout") == subprocess.PIPE
          and (kw.get("text") is True or bool(kw.get("encoding"))), kw)

    saved = A.__dict__.get("VM_STAT_TIMEOUT_S", _MISSING)
    try:
        A.VM_STAT_TIMEOUT_S = 2.5
        with _RunRecorder(_canned) as rec:
            T()
        check("... its bound is VM_STAT_TIMEOUT_S as it reads when vm_stat runs (set to 2.5 -> timeout 2.5), never "
              "a second copy of the number", len(rec.calls) == 1 and _is_vm_stat_call(rec.calls[0], 2.5), rec.calls)

        A.VM_STAT_TIMEOUT_S = 0.5
        t0 = time.monotonic()
        hung = T(list(SLEEPER))
        took = time.monotonic() - t0
        check(f"a call with no timeout argument on a child that sleeps {SLEEP_S} s is ended at VM_STAT_TIMEOUT_S "
              "(set to 0.5) -> None, well before the child would end (and before the 5 s default)",
              hung is None and took < 4.5, (hung, round(took, 2)))

        with _Platform(A, "darwin", sysconf=sysconf_for(M1_14_TOTAL, 16384), real_vm_stat=True), \
                _RunRecorder(_swap_for_sleeper) as rec:
            t0 = time.monotonic()
            got = A._memory()
            took = time.monotonic() - t0
        check(f"on a Mac whose vm_stat hangs, _memory() (what R13 and _before_embed call) returns (None, total) at "
              f"VM_STAT_TIMEOUT_S (0.5): the module's own call to [/usr/bin/vm_stat], run for real with only the "
              f"program swapped for a child that sleeps {SLEEP_S} s",
              got == (None, M1_14_TOTAL) and took < 4.5 and len(rec.calls) == 1
              and _is_vm_stat_call(rec.calls[0], 0.5), (got, round(took, 2), rec.calls))
    finally:
        if saved is _MISSING:
            A.__dict__.pop("VM_STAT_TIMEOUT_S", None)
        else:
            A.VM_STAT_TIMEOUT_S = saved

    with _Platform(A, "darwin", sysconf=sysconf_for(M1_14_TOTAL, 16384), real_vm_stat=True), \
            _RunRecorder(_canned) as rec:
        dm = DM()
        mem = A._memory()
    check("_darwin_memory() and _memory() on a Mac go through that call: one run each of [/usr/bin/vm_stat] with "
          "timeout 5 s, its output read as (available, total)",
          dm == mem == (M1_14_AVAIL, M1_14_TOTAL) and len(rec.calls) == 2
          and all(_is_vm_stat_call(c, 5.0) for c in rec.calls), (dm, mem, rec.calls))
    check("the real subprocess.run is back after the recorder", subprocess.run is _REAL_RUN, subprocess.run)


# =====================================================================================================================
# D — the runner's floor and pre-embed wait on a Mac
# =====================================================================================================================
def gate_runner_floor(A, tmp: Path) -> None:
    root = tmp / "proj"
    (root / ".deepmap").mkdir(parents=True, exist_ok=True)
    saved = {n: getattr(A, n) for n in ("_model_state", "_now", "_sleep")}
    clock = [1_000_000.0]
    sleeps: list = []

    def now():
        return clock[0]

    def sleep_(s):
        sleeps.append(s)
        clock[0] += float(s)
    A._model_state, A._now, A._sleep = (lambda: {"complete": True}), now, sleep_
    try:
        with _Platform(A, "darwin", sysconf=sysconf_for(M1_8GB, 16384), vm_stat=PRESSURE_8GB):
            R = A._Runner(root, "session-start", None, dict(os.environ), clock[0])
            try:
                R._r13()
                end = None
            except A._End as e:
                end = e
        check("an 8 GB Mac with 1.6 GB available: R13 skips the refresh (skipped:low-memory, why_code low-memory)",
              end is not None and end.outcome == "skipped:low-memory" and end.fields.get("why_code") == "low-memory",
              None if end is None else (end.outcome, end.fields))
        reason = str((end.fields if end is not None else {}).get("reason") or "")
        check("... its reason names the reading and the floor (1.6 GB of 3.0 GB)", "1.6 GB" in reason and
              "3.0 GB" in reason, reason)
        with _Platform(A, "darwin", sysconf=sysconf_for(M1_14_TOTAL, 16384), vm_stat=M1_14_16GB):
            R = A._Runner(root, "session-start", None, dict(os.environ), clock[0])
            try:
                R._r13()
                end = None
            except A._End as e:
                end = e
        check("a 16 GB Mac with 6.0 GB available (floor 4 GiB): R13 lets the refresh run",
              end is None, None if end is None else (end.outcome, end.fields))

        sleeps.clear()
        with _Platform(A, "darwin", sysconf=sysconf_for(M1_8GB, 16384), vm_stat=PRESSURE_8GB) as P:
            R = A._Runner(root, "session-start", None, dict(os.environ), clock[0])
            got = R._before_embed()
        check("memory that stays low on a Mac: the pre-embed wait polls every 10 s for 2 minutes, then postpones the "
              "embeddings (False)", got is False and sum(sleeps) >= 120.0 and len(sleeps) == 12 and P.vm_calls == 13,
              (got, len(sleeps), sum(sleeps), P.vm_calls))
        feed = iter([PRESSURE_8GB, PRESSURE_8GB, ROOM_8GB])
        sleeps.clear()
        with _Platform(A, "darwin", sysconf=sysconf_for(M1_8GB, 16384), vm_stat=lambda: next(feed, ROOM_8GB)):
            R = A._Runner(root, "session-start", None, dict(os.environ), clock[0])
            got = R._before_embed()
        check("memory that recovers mid-wait on a Mac: the embeddings run (True) after two polls",
              got is True and len(sleeps) == 2, (got, sleeps))
        sleeps.clear()
        with _Platform(A, "darwin", sysconf=sysconf_for(M1_8GB, 16384), vm_stat=None):
            R = A._Runner(root, "session-start", None, dict(os.environ), clock[0])
            got = R._before_embed()
        check("a Mac whose vm_stat cannot be read: no wait, the embeddings run (as before: no reading, no floor)",
              got is True and not sleeps, (got, sleeps))
    finally:
        for n, f in saved.items():
            setattr(A, n, f)


# =====================================================================================================================
# E — shapes that must not change (Windows, Linux, the real platform)
# =====================================================================================================================
def gate_unchanged(A) -> None:
    with _Platform(A, "linux", sysconf=sysconf_for(32 * GIB, 4096), vm_stat=M1_14_16GB, meminfo=MEMINFO) as P:
        got = A._memory()
    check("Linux: /proc/meminfo MemAvailable / MemTotal (kB x 1024), as before",
          got == (9822104 * 1024, 16303428 * 1024), got)
    check("... and Linux never runs vm_stat", P.vm_calls == 0, P.vm_calls)
    old = MEMINFO.replace("MemAvailable:    9822104 kB\n", "")
    with _Platform(A, "linux", sysconf=sysconf_for(32 * GIB, 4096), vm_stat=M1_14_16GB, meminfo=old) as P:
        got = A._memory()
    check("Linux without MemAvailable (an old kernel): available None — never a vm_stat or other guess",
          got == (None, 16303428 * 1024) and P.vm_calls == 0, (got, P.vm_calls))
    with _Platform(A, "linux", sysconf=sysconf_for(32 * GIB, 4096), vm_stat=M1_14_16GB, meminfo=None) as P:
        got = A._memory()
    check("Linux without /proc: (None, sysconf's total), no vm_stat", got == (None, 32 * GIB) and P.vm_calls == 0,
          (got, P.vm_calls))
    with _Platform(A, "freebsd14", sysconf=sysconf_for(16 * GIB, 4096), vm_stat=M1_14_16GB, meminfo=None) as P:
        got = A._memory()
    check("another POSIX system (FreeBSD): (None, sysconf's total), no vm_stat — only macOS runs it",
          got == (None, 16 * GIB) and P.vm_calls == 0, (got, P.vm_calls))

    # the real platform (Windows here, Linux in CI): its own reading, and vm_stat never runs
    calls = []
    saved = A.__dict__.get("_vm_stat_text", _MISSING)
    A._vm_stat_text = lambda *a, **k: calls.append(1) or M1_14_16GB
    try:
        got = A._memory()
    finally:
        if saved is _MISSING:
            A.__dict__.pop("_vm_stat_text", None)
        else:
            A._vm_stat_text = saved
    if sys.platform == "darwin":
        skip("the real platform's own reading without vm_stat", "this machine is a Mac (section C read its vm_stat)")
        return
    avail, total = got if isinstance(got, tuple) and len(got) == 2 else (None, None)
    ok = (isinstance(total, int) and total > 0 and isinstance(avail, int) and 0 <= avail <= total)
    check(f"this machine ({sys.platform}): _memory() reads its own (available, total) as before", ok, got)
    check(f"... and never runs vm_stat on {sys.platform}", not calls, len(calls))


def main() -> int:
    print("auto-sync on macOS: the available memory a Mac reports (vm_stat) reaches the low-memory floor")
    try:
        A = _load()
    except Exception as exc:  # noqa: BLE001
        check("build/auto_sync.py imports", False, f"{type(exc).__name__}: {exc}")
        A = None
    tmp = Path(tempfile.mkdtemp(prefix="dm_macmem_"))
    try:
        if A is not None:
            _section("A. the darwin dispatch through the real _memory()", gate_dispatch, A)
            _section("B. the vm_stat parser", gate_parser, A)
            _section("C. the vm_stat runner (bounded, never raises)", gate_runner_child, A, tmp)
            _section("C. the default call (_darwin_memory's): exactly /usr/bin/vm_stat, bounded by VM_STAT_TIMEOUT_S",
                     gate_default_call, A)
            _section("D. the runner's floor and pre-embed wait on a Mac", gate_runner_floor, A, tmp)
            _section("E. shapes that must not change (Linux, other POSIX, the real platform)", gate_unchanged, A)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)          # exactly the temp dir this gate created
    check("hermetic: the gate's own temp dir is gone", not tmp.exists(), str(tmp))
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 76)
    if SKIPS:
        print(f"({len(SKIPS)} skipped: {', '.join(SKIPS)[:300]})")
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
