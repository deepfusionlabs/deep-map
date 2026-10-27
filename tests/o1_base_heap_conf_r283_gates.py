#!/usr/bin/env python3
"""ISS-build:020 (C12) — the BASE store (every machine's first project) gets the same bounded memory as every instance.

Finding: DEEPMAP_NEO4J_HEAP_MAX and the documented 512m heap cap lived only in the per-instance conf block
(conf_instance_block). The first project on a machine runs the base pair (7687/7474) with the LEGACY layout, whose conf
edit (configure_neo4j(project_key=None)) appended only the STAGE-0 block — auth minimum length + telemetry — once,
marker-gated. Its JVM therefore ran with Neo4j's default heap, and the remedy the CLI / retriever print for a memory
error ("raise DEEPMAP_NEO4J_HEAP_MAX; applied when the instance restarts") did nothing there.

The fix: the legacy block carries the same memory keys (one helper for both blocks), and the legacy conf edit is now
content-addressed and strict-validation-safe like the per-instance one: an earlier copy of DeepMap's block and every
ACTIVE line setting one of its keys are replaced, so a heap change is picked up on the next start and no key is ever
active twice (Neo4j 5 refuses to start on a duplicate). The heap value is validated (a value Neo4j can't parse would now
stop the base store too), and the initial heap never exceeds the cap.

Hermetic: temp DEEPMAP_HOME with a fake extracted Neo4j conf; ensure_neo4j's Neo4j/JVM seams are stubbed (nothing is
downloaded, started or connected).
Usage: python tests/o1_base_heap_conf_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []
VER = "5.26.0"

SHIPPED = """\
# Neo4j configuration (shipped defaults, trimmed)
server.directories.import=import
#server.default_listen_address=0.0.0.0
server.bolt.enabled=true
#server.bolt.listen_address=:7687
server.http.enabled=true
server.https.enabled=false
#server.memory.heap.initial_size=512m
#server.memory.heap.max_size=512m
#server.memory.pagecache.size=10g
#dbms.usage_report.enabled=true
"""
# the block every build <= 0.28.1 appended (marker-gated, once)
OLD_BLOCK = ("\n# ===== DeepMap provisioner settings (auto-added) =====\n"
             "# FD-1.6: relax min password length so the DeepMap dev password 'deepmap' (7 chars) is accepted\n"
             "dbms.security.auth_minimum_password_length=4\n"
             "# TELEMETRY-1: zero-telemetry / air-gap — disable Neo4j anonymous usage + client telemetry\n"
             "dbms.usage_report.enabled=false\n"
             "client.allow_telemetry=false\n")
MEMORY_KEYS = ("server.memory.heap.initial_size", "server.memory.heap.max_size", "server.memory.pagecache.size")
STAGE0_KEYS = ("dbms.security.auth_minimum_password_length", "dbms.usage_report.enabled", "client.allow_telemetry")


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {str(detail)[:700]}"))


def _active(text: str) -> list:
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, v = s.split("=", 1)
            out.append((k.strip(), v.strip()))
    return out


def _val(text: str, key: str):
    vals = [v for k, v in _active(text) if k == key]
    return vals[0] if len(vals) == 1 else vals


def _dupes(text: str) -> list:
    keys = [k for k, _v in _active(text)]
    return sorted({k for k in keys if keys.count(k) > 1})


def _quiet(fn, *a, **k):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        r = fn(*a, **k)
    return r, out.getvalue() + err.getvalue()


def _load(root: Path):
    sys.path.insert(0, str(root / "build"))
    import provision as P  # noqa: E402
    return P


class _Heap:
    def __init__(self, value):
        self.value, self.saved = value, None

    def __enter__(self):
        self.saved = os.environ.get("DEEPMAP_NEO4J_HEAP_MAX")
        if self.value is None:
            os.environ.pop("DEEPMAP_NEO4J_HEAP_MAX", None)
        else:
            os.environ["DEEPMAP_NEO4J_HEAP_MAX"] = self.value
        return self

    def __exit__(self, *exc):
        if self.saved is None:
            os.environ.pop("DEEPMAP_NEO4J_HEAP_MAX", None)
        else:
            os.environ["DEEPMAP_NEO4J_HEAP_MAX"] = self.saved


def gate_a_fresh(P, conf: Path) -> None:
    print("\n-- A: a fresh shipped conf -> the base block carries the bounded memory, every key active once --")
    conf.write_text(SHIPPED, encoding="utf-8")
    with _Heap(None):
        first, _ = _quiet(P.configure_neo4j, VER)
        txt = conf.read_text(encoding="utf-8")
        check("first configure writes (True)", first is True, repr(first))
        check("heap cap 512m (the documented default) on the base store", _val(txt, "server.memory.heap.max_size") == "512m",
              _active(txt))
        check("initial heap 256m and page cache 192m, like every instance",
              _val(txt, "server.memory.heap.initial_size") == "256m"
              and _val(txt, "server.memory.pagecache.size") == "192m", _active(txt))
        check("the STAGE-0 keys are still written (auth minimum length + telemetry off)",
              _val(txt, "dbms.security.auth_minimum_password_length") == str(P.MIN_PW_LEN)
              and _val(txt, "dbms.usage_report.enabled") == "false" and _val(txt, "client.allow_telemetry") == "false")
        check("no key is active twice (strict validation)", _dupes(txt) == [], _dupes(txt))
        check("exactly one DeepMap block (CONF_MARKER once)", txt.count(P.CONF_MARKER) == 1, txt.count(P.CONF_MARKER))
        check("shipped keys DeepMap does not manage survive verbatim",
              all(x in txt for x in ("server.directories.import=import", "server.bolt.enabled=true",
                                     "server.https.enabled=false", "#server.memory.pagecache.size=10g")))
        again, _ = _quiet(P.configure_neo4j, VER)
        check("a second configure is a content-addressed no-op (False, file unchanged)",
              again is False and conf.read_text(encoding="utf-8") == txt, repr(again))


def gate_b_upgrade(P, conf: Path) -> None:
    print("\n-- B: a base conf an older build configured (the STAGE-0 block only) is upgraded in place --")
    conf.write_text(SHIPPED + OLD_BLOCK, encoding="utf-8")
    with _Heap(None):
        changed, _ = _quiet(P.configure_neo4j, VER)
    txt = conf.read_text(encoding="utf-8")
    check("the old block is rewritten (True) — it used to be left as is ('already has DeepMap block')",
          changed is True, repr(changed))
    check("the base store's conf now caps the heap at 512m", _val(txt, "server.memory.heap.max_size") == "512m",
          _active(txt))
    check("no key is active twice after the upgrade", _dupes(txt) == [], _dupes(txt))
    check("one DeepMap header, one FD-1.6 comment (the old copy was replaced, not appended to)",
          txt.count(P.CONF_MARKER) == 1 and txt.count("# FD-1.6:") == 1, txt[-700:])


def gate_c_heap_changes(P, conf: Path) -> None:
    print("\n-- C: DEEPMAP_NEO4J_HEAP_MAX is picked up on the next start (the remedy the CLI prints works) --")
    conf.write_text(SHIPPED, encoding="utf-8")
    with _Heap(None):
        _quiet(P.configure_neo4j, VER)
    with _Heap("1g"):
        changed, _ = _quiet(P.configure_neo4j, VER)
        txt = conf.read_text(encoding="utf-8")
        check("DEEPMAP_NEO4J_HEAP_MAX=1g -> rewritten with a 1g cap", changed is True
              and _val(txt, "server.memory.heap.max_size") == "1g", _active(txt))
        again, _ = _quiet(P.configure_neo4j, VER)
        check("... then a no-op", again is False)
    with _Heap(None):
        changed, _ = _quiet(P.configure_neo4j, VER)
        txt = conf.read_text(encoding="utf-8")
        check("unset again -> back to 512m", changed is True and _val(txt, "server.memory.heap.max_size") == "512m",
              _active(txt))
        check("still no duplicate active key", _dupes(txt) == [], _dupes(txt))


def gate_d_user_duplicates(P, conf: Path) -> None:
    print("\n-- D: an active line setting one of DeepMap's keys is replaced, never duplicated --")
    conf.write_text(SHIPPED + "server.memory.heap.max_size=2g\ndbms.security.auth_minimum_password_length=8\n"
                    + OLD_BLOCK, encoding="utf-8")
    with _Heap(None):
        _quiet(P.configure_neo4j, VER)
    txt = conf.read_text(encoding="utf-8")
    check("no duplicate active key (Neo4j 5 strict validation would refuse to start)", _dupes(txt) == [], _dupes(txt))
    check("DeepMap's values are the ones in force", _val(txt, "server.memory.heap.max_size") == "512m"
          and _val(txt, "dbms.security.auth_minimum_password_length") == str(P.MIN_PW_LEN), _active(txt))


def gate_e_bad_values(P, conf: Path) -> None:
    print("\n-- E: a heap value Neo4j could not parse never reaches a conf; the initial heap never exceeds the cap --")
    for label, value, want in (("garbage", "lots", "512m"), ("empty", "", "512m"), ("whitespace", "   ", "512m"),
                               ("a line injection", "1g\nserver.bolt.listen_address=0.0.0.0:7687", "512m"),
                               ("a negative size", "-1g", "512m"), ("a unit Neo4j does not know", "2x", "512m"),
                               ("upper-case unit", "1G", "1G"), ("a spaced size", "768 MiB", "768MiB"),
                               ("plain bytes", "536870912", "536870912")):
        conf.write_text(SHIPPED, encoding="utf-8")
        with _Heap(value):
            _r, log = _quiet(P.configure_neo4j, VER)
        txt = conf.read_text(encoding="utf-8")
        check(f"{label} ({value!r}) -> heap.max_size={want}", _val(txt, "server.memory.heap.max_size") == want
              and _dupes(txt) == [] and "0.0.0.0:7687" not in txt, f"{_active(txt)} {log[-200:]}")
        if want == "512m" and value.strip():
            check(f"... {label}: the refusal is said (WARNING naming DEEPMAP_NEO4J_HEAP_MAX)",
                  "DEEPMAP_NEO4J_HEAP_MAX" in log and "WARNING" in log, log[-300:])
    for label, value, init in (("128m", "128m", "128m"), ("200MiB", "200MiB", "200MiB"), ("256m", "256m", "256m"),
                               ("1g", "1g", "256m")):
        conf.write_text(SHIPPED, encoding="utf-8")
        with _Heap(value):
            _quiet(P.configure_neo4j, VER)
            blk = P.conf_instance_block("/d", "/l", "/r", 7689, 7476)
        txt = conf.read_text(encoding="utf-8")
        check(f"cap {label}: the base conf's initial heap is {init} (never above the cap: the JVM refuses that)",
              _val(txt, "server.memory.heap.initial_size") == init, _active(txt))
        check(f"cap {label}: the instance block's initial heap is {init} too",
              _val(blk, "server.memory.heap.initial_size") == init, blk)


def gate_f_instance_block_unchanged(P) -> None:
    print("\n-- F: the per-instance block is unchanged at the default --")
    with _Heap(None):
        blk = P.conf_instance_block("/d", "/l", "/r", 7689, 7476)
    for line in ("# Bounded per-instance memory (override DEEPMAP_NEO4J_HEAP_MAX)", "server.memory.heap.initial_size=256m",
                 "server.memory.heap.max_size=512m", "server.memory.pagecache.size=192m"):
        check(f"instance block keeps `{line}`", line in blk.splitlines(), blk)


def gate_g_seeded_instance(P, conf: Path) -> None:
    print("\n-- G: an instance seeded from the upgraded base conf still has every managed key active once --")
    conf.write_text(SHIPPED + OLD_BLOCK, encoding="utf-8")
    (conf.parent / "server-logs.xml").write_text("<config/>", encoding="utf-8")
    with _Heap(None):
        _quiet(P.configure_neo4j, VER)
        _quiet(P.configure_neo4j, VER, project_key="aaaa0000bbbb", bolt=7689, http=7476)
    txt = P.paths(VER, "aaaa0000bbbb")["conf"].read_text(encoding="utf-8")
    check("no duplicate active key in the seeded instance conf", _dupes(txt) == [], _dupes(txt))
    keys = [k for k, _v in _active(txt)]
    check("every memory key active exactly once there", all(keys.count(k) == 1 for k in MEMORY_KEYS),
          [(k, keys.count(k)) for k in MEMORY_KEYS])


def gate_h_ensure_base(P, conf: Path) -> None:
    print("\n-- H: ensure_neo4j on the BASE store (project_key None) writes the capped conf before the start --")
    conf.write_text(SHIPPED + OLD_BLOCK, encoding="utf-8")
    seen = {}

    def fake_start(version, project_key=None):
        seen["conf_at_start"] = conf.read_text(encoding="utf-8")
        seen["key"] = project_key
        return 4242

    stubs = {"_bolt_state": lambda *a, **k: "down", "preflight": lambda *a, **k: True,
             "_download_extract_shared": lambda v: None, "set_initial_password": lambda *a, **k: True,
             "start_neo4j": fake_start, "wait_for_bolt": lambda *a, **k: True,
             "_record_neo4j_server_identity": lambda *a, **k: None, "_is_initialized": lambda *a, **k: True,
             "_shared_binary_present": lambda *a, **k: True}
    saved = {n: getattr(P, n) for n in stubs}
    try:
        for n, f in stubs.items():
            setattr(P, n, f)
        with _Heap(None):
            ok, _ = _quiet(P.ensure_neo4j, uri="bolt://127.0.0.1:7687", user="neo4j", password="deepmap",
                           version=VER, project_key=None)
    finally:
        for n, f in saved.items():
            setattr(P, n, f)
    at = seen.get("conf_at_start", "")
    check("ensure_neo4j(base) succeeded through the stubbed start", ok is True and seen.get("key") is None, repr(ok))
    check("the base Neo4j starts on a conf capping its heap at 512m",
          _val(at, "server.memory.heap.max_size") == "512m" and _dupes(at) == [], _active(at))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    root = REPO
    if "--root" in sys.argv:
        root = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()
    print("=" * 72)
    print(f"DeepMap — the base store's Neo4j conf caps the heap like every instance (root: {root})")
    print("=" * 72)
    for k in ("NEO4J_URI", "DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT", "NEO4J_VERSION"):
        os.environ.pop(k, None)
    with tempfile.TemporaryDirectory(prefix="o1heap") as td:
        os.environ["DEEPMAP_HOME"] = str(Path(td) / "home")
        P = _load(root)
        conf = P.paths(VER)["conf"]
        conf.parent.mkdir(parents=True, exist_ok=True)
        gate_a_fresh(P, conf)
        gate_b_upgrade(P, conf)
        gate_c_heap_changes(P, conf)
        gate_d_user_duplicates(P, conf)
        gate_e_bad_values(P, conf)
        gate_f_instance_block_unchanged(P)
        gate_g_seeded_instance(P, conf)
        gate_h_ensure_base(P, conf)
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
