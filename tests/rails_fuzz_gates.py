#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hardening gates for the Ruby/Rails recognizer: seeded fuzz + metamorphic properties + the
integration MONEY GATE (sync-entry -> Cypher-out on a fake driver). No Neo4j, no Ruby runtime.

The rails_edges_gates suite proves the recognizer is CORRECT on conventional Rails source; this
suite proves it is SAFE on everything else and WIRED correctly end-to-end:

  - FUZZ (300 seeded adversarial sources — heredocs, unicode, binary junk, truncated decls, deep
    nesting, huge lines, CRLF/CR, DSL-in-strings): recognize() NEVER raises, always returns
    (list, list), every edge is a TypedEdge with an edge_type the ruby entry declared, every node
    either survives the validator or is dropped silently — and the whole pipeline is DETERMINISTIC
    (same input -> byte-equal output);
  - METAMORPHIC properties: comment insertion is edge-invariant; a blank-line prefix shifts node
    start_lines by exactly N and changes nothing else; recognize() is idempotent; deleting an
    association line removes exactly its edges;
  - the MONEY GATE: a real Rails tree through sync._apply_recognizers -> sync.push_to_neo4j on a
    fake driver (the confidence_gates idiom) in the WORST field state (base extractor returned
    NOTHING, the ast-fallback case): ensured :Class nodes MERGE with the label whitelist, CASCADES
    MERGEs REVERSED with the right keys, an unresolvable HOOKS target DROPS (SAME_FILE_STRICT),
    and adding the def-derived :Method node makes the same HOOKS edge resolve EXTRACTED — proving
    the full recognizer->validator->merge->resolve->prune->push chain without Neo4j;
  - HOOK payload fuzz: 100 seeded malformed SessionStart payloads -> the notify hook always exits
    0 and never writes under the project root.
"""
from __future__ import annotations

import io
import json
import os
import random
import string
import sys
import tempfile
from pathlib import Path

# Auto-sync (DeepMap's background refresh at session start) is on by default in an attended session; this gate
# locks the hooks WITHOUT it, so it is pinned off for this process and every hook process the gate starts
# (tests/auto_sync_hooks_gates.py locks the hooks with it on).
os.environ["DEEPMAP_AUTO_SYNC"] = "0"
REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
HOOKS = REPO / "hooks"
sys.path.insert(0, str(BUILD))

import ruby_rails_edges as RR       # noqa: E402
import language_recognizers as LR   # noqa: E402
import sync as S                    # noqa: E402
import framework_edges as FE        # noqa: E402
import edge_resolve as ER           # noqa: E402

_PASS = _FAIL = 0


def check(desc: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    ok = bool(cond)
    _PASS += ok
    _FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc}" + ("" if ok else f"  -- {detail}"))


# ---- fuzz corpus -------------------------------------------------------------
_DSL_FRAGMENTS = [
    "has_many :items, dependent: :destroy", "belongs_to :owner", "has_one :thing",
    "before_save :fix", "after_destroy :log_it, if: :active?", "validates :x, presence: true",
    "scope :recent, -> { where(a: 1) }", "scope :active, lambda { where(active: true) }",
    "scope :old, proc { where('x') }", "scope :n, Proc.new { order(:id) }", "scope module: 'x' do",
    "class Foo < ApplicationRecord", "class Bar",
    "module Baz", "end", "def go", "  x = 1", "has_and_belongs_to_many :tags",
    "has_many :ghosts, class_name: 'Ns::Ghost', dependent: :delete_all",
    "around_update :guard", "# has_many :commented", "  <<~HEREDOC", "HEREDOC",
    'puts "class Fake < ApplicationRecord"', "if x", "unless y", "while z", "\t\tend",
    # routes.rb DSL (the `routes.draw` trigger flips recognize() into route mode even here).
    "Rails.application.routes.draw do", "root 'home#index'", "get '/x', to: 'x#show'",
    "get '/y/:id' => 'admin/y#edit'", "resources :items", "resource :profile",
    "namespace :admin do", "scope :v2 do", "scope module: 'internal' do",
    "constraints(lambda { |r| true }) do", "match 'p', to: 'p#q', via: :get", "  end",
]
_JUNK = ["\x00\x01\xff", "日本語のテキスト", "🚀🔥", "a" * 5000, "class", "<", "::", ":",
         "dependent:", "has_many", "class  <  ", "class 9Bad < X", "end end end end",
         "\r\n\r\r\n", "＜クラス＞", "-- not ruby --", "{\"json\": true}",
         "resources", "get", "routes.draw", "->", "#=>", "get 'x' =>", "namespace :"]

# Route-only fragments for the dedicated routes-mode fuzz (rel = config/routes.rb).
_ROUTE_FRAGMENTS = [
    "Rails.application.routes.draw do", "root 'home#index'", "get '/orders', to: 'orders#index'",
    "post '/orders' => 'orders#create'", "get '/o/:id', to: 'admin/orders#show'",
    "resources :line_items", "resource :profile", "resources :posts do", "member do",
    "namespace :admin do", "scope :v2 do", "scope module: 'api', path: '/api' do",
    "constraints(subdomain: 'api') do", "constraints(lambda { |req| req.format == :json }) do",
    "match 'photos/:id', to: 'photos#show', via: [:get, :post]", "mount Sidekiq::Web => '/sidekiq'",
    "end", "  end", "# get '/commented', to: 'x#y'", "delete '/o/:id' => 'orders#destroy'",
]


def _fuzz_source(rng: random.Random) -> str:
    n = rng.randint(0, 40)
    lines = []
    for _ in range(n):
        r = rng.random()
        if r < 0.55:
            lines.append(rng.choice(_DSL_FRAGMENTS))
        elif r < 0.75:
            lines.append(rng.choice(_JUNK))
        else:
            lines.append("".join(rng.choice(string.printable) for _ in range(rng.randint(0, 80))))
    eol = rng.choice(["\n", "\r\n", "\r"])
    return eol.join(lines)


def gate_fuzz() -> None:
    print("\n-- 300 seeded adversarial sources: never raise, deterministic, vocab-clean --")
    rng = random.Random(20260709)
    ruby_entry = next(e for e in LR.REGISTRY if e.name == "ruby")
    crashed = nondet = badvocab = badshape = badnode = 0
    for i in range(300):
        src = _fuzz_source(rng)
        rel = f"app/models/f{i}.rb"
        try:
            out1 = RR.recognize(src, rel)
            out2 = RR.recognize(src, rel)
        except Exception:
            crashed += 1
            continue
        if not (isinstance(out1, tuple) and len(out1) == 2
                and isinstance(out1[0], list) and isinstance(out1[1], list)):
            badshape += 1
            continue
        if out1 != out2:
            nondet += 1
        if any(e.edge_type not in ruby_entry.edge_types for e in out1[1]):
            badvocab += 1
        # supplemental nodes (incl. the A1 model-scope :Method nodes) must survive the validator.
        if len(LR.validate_supplemental(out1[0], "ruby")) != len(out1[0]):
            badnode += 1
    check("0/300 raised", crashed == 0, f"{crashed} crashed")
    check("0/300 wrong return shape", badshape == 0, f"{badshape}")
    check("0/300 nondeterministic", nondet == 0, f"{nondet}")
    check("0/300 emitted an undeclared edge type", badvocab == 0, f"{badvocab}")
    check("0/300 produced a validator-rejected supplemental node", badnode == 0, f"{badnode}")


def _fuzz_route_source(rng: random.Random) -> str:
    n = rng.randint(0, 40)
    lines = []
    for _ in range(n):
        r = rng.random()
        if r < 0.6:
            lines.append(rng.choice(_ROUTE_FRAGMENTS))
        elif r < 0.8:
            lines.append(rng.choice(_JUNK))
        else:
            lines.append("".join(rng.choice(string.printable) for _ in range(rng.randint(0, 80))))
    return rng.choice(["\n", "\r\n", "\r"]).join(lines)


def gate_routes_fuzz() -> None:
    print("\n-- 300 seeded adversarial routes.rb sources: never raise, deterministic, vocab-clean --")
    rng = random.Random(20260712)
    ruby_entry = next(e for e in LR.REGISTRY if e.name == "ruby")
    crashed = nondet = badvocab = badshape = badnode = 0
    for i in range(300):
        src = _fuzz_route_source(rng)
        rel = f"config/routes.rb" if i % 2 else f"config/routes/f{i}.rb"
        try:
            out1 = RR.recognize(src, rel)
            out2 = RR.recognize(src, rel)
        except Exception:
            crashed += 1
            continue
        if not (isinstance(out1, tuple) and len(out1) == 2
                and isinstance(out1[0], list) and isinstance(out1[1], list)):
            badshape += 1
            continue
        if out1 != out2:
            nondet += 1
        if any(e.edge_type not in ruby_entry.edge_types for e in out1[1]):
            badvocab += 1
        # supplemental route nodes must survive the validator (no KeyError / bad label).
        if len(LR.validate_supplemental(out1[0], "ruby")) != len(out1[0]):
            badnode += 1
    check("0/300 raised (route mode)", crashed == 0, f"{crashed} crashed")
    check("0/300 wrong return shape (route mode)", badshape == 0, f"{badshape}")
    check("0/300 nondeterministic (route mode)", nondet == 0, f"{nondet}")
    check("0/300 emitted an undeclared edge type (route mode)", badvocab == 0, f"{badvocab}")
    check("0/300 produced a validator-rejected supplemental node", badnode == 0, f"{badnode}")


ORDER_SRC = """class Order < ApplicationRecord
  has_many :line_items, dependent: :destroy
  belongs_to :customer
  before_save :normalize_totals

  def normalize_totals
    self.total = 0
  end
end
"""


def gate_metamorphic() -> None:
    print("\n-- metamorphic properties --")
    rel = "app/models/order.rb"
    n0, e0 = RR.recognize(ORDER_SRC, rel)

    # Idempotence.
    check("idempotent (recognize twice == same)", RR.recognize(ORDER_SRC, rel) == (n0, e0))

    # Comment insertion: edges invariant.
    commented = ORDER_SRC.replace("  belongs_to :customer",
                                  "  # a comment\n  belongs_to :customer")
    _, e1 = RR.recognize(commented, rel)
    check("comment insertion is edge-invariant", e1 == e0)

    # Blank-line prefix: node start_lines shift by exactly N; names/edges identical.
    N = 3
    n2, e2 = RR.recognize(("\n" * N) + ORDER_SRC, rel)
    check("prefix of N blank lines shifts node start_line by exactly N",
          [(n["name"], n["start_line"]) for n in n2]
          == [(n["name"], n["start_line"] + N) for n in n0],
          f"{[(n['name'], n['start_line']) for n in n2]}")
    check("prefix of N blank lines leaves edges identical", e2 == e0)

    # Deleting the has_many line removes EXACTLY its HAS_MANY + CASCADES edges.
    no_hm = "\n".join(l for l in ORDER_SRC.splitlines() if "has_many" not in l)
    _, e3 = RR.recognize(no_hm, rel)
    gone = [e for e in e0 if e not in e3]
    check("deleting an association removes exactly its HAS_MANY+CASCADES edges",
          sorted(e.edge_type for e in gone) == ["CASCADES", "HAS_MANY"]
          and all(e.dst_name == "LineItem" for e in gone), str(gone))
    check("everything else survives",
          all(e in e3 for e in e0 if e.dst_name != "LineItem"))


class _Sess:
    def __init__(self, log):
        self.log = log

    def run(self, cypher, **params):
        for row in params.get("rows") or [params]:   # the push batches with UNWIND $rows: one entry per row
            self.log.append((cypher, row))

    def begin_transaction(self):                     # the edge rebuild runs inside a write transaction
        return self

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Drv:
    def __init__(self, log):
        self._log = log

    def session(self):
        return _Sess(self._log)


def gate_money() -> None:
    print("\n-- MONEY GATE: Rails tree -> _apply_recognizers -> push_to_neo4j (fake driver) --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "app" / "models").mkdir(parents=True)
        (root / "app" / "models" / "order.rb").write_text(ORDER_SRC, encoding="utf-8")
        (root / "app" / "models" / "line_item.rb").write_text(
            "class LineItem < ApplicationRecord\n  belongs_to :order\nend\n", encoding="utf-8")

        # THE WORST FIELD STATE: the base extractor returned NOTHING (ast-fallback on .rb). F2a: the
        # recognizer now also node-ifies `def normalize_totals` (Order.normalize_totals), so the empty
        # model layer is backfilled with the Class nodes AND the method layer.
        merged, typed = S._apply_recognizers(root, [])
        check("recognizer fills the empty model+method layer (ensured :Class + F2a def :Method nodes)",
              {n["name"] for n in merged} == {"Order", "LineItem", "Order.normalize_totals"},
              str({n.get("name") for n in merged}))

        log: list = []
        n_nodes, _ = S.push_to_neo4j(_Drv(log), merged, [], [], typed)
        check("all three nodes pushed (2 Class + 1 F2a def Method)", n_nodes == 3)
        node_merges = [c for c, _ in log if "MERGE (x:Class" in c]
        check("ensured Class nodes MERGE with the :Class label", len(node_merges) == 2, str(node_merges))
        method_merges = [c for c, _ in log if "MERGE (x:Method" in c]
        check("F2a def node MERGEs with the :Method label", len(method_merges) == 1, str(method_merges))

        cas = [(c, p) for c, p in log if "MERGE" in c and "CASCADES" in c]
        check("CASCADES MERGEs REVERSED with the right keys "
              "(b=LineItem -> a=Order: impact('Order') will list LineItem)",
              len(cas) == 1 and "MERGE (b)-[r:CASCADES]->(a)" in cas[0][0]
              and "Order:1" in cas[0][1]["a"] and "LineItem:1" in cas[0][1]["b"], str(cas))

        # F2a field win: the recognizer node-ifies `def normalize_totals`, so the
        # `before_save :normalize_totals` callback now RESOLVES same-file -> EXTRACTED, instead of
        # dropping because its target def was never node-ified (12/54 HOOKS edges recovered in the field).
        hooks = [(c, p) for c, p in log if "MERGE" in c and "HOOKS" in c]
        check("F2a: HOOKS to a same-file def RESOLVES (recognizer node-ifies the def) -> EXTRACTED",
              len(hooks) == 1 and hooks[0][1].get("c") == "EXTRACTED", str(hooks))

        # Drop-not-fabricate is still honored: a callback whose target is NEITHER a def NOR a scope
        # (no node anywhere in the file) still DROPS — the recognizer never fabricates a HOOKS target.
        gnodes, gedges = RR.recognize(
            "class Ghost < ApplicationRecord\n  after_save :vanished_helper\nend\n",
            "app/models/ghost.rb")
        gcounts = ER.push_typed_edges(_Sess([]), [e for e in gedges if e.edge_type == "HOOKS"],
                                      ER.Resolver(gnodes), [n["key"] for n in gnodes])
        check("HOOKS to a symbol with no def/scope node still DROPS (no fabrication)",
              gcounts.get("HOOKS", 0) == 0, str(gcounts))

        # The anti-phantom prune ran for the full vocabulary (present + stale types).
        pruned = {c.split(":")[1].split("]")[0] for c, _ in log if "DELETE r" in c and "[r:" in c}
        check("every whitelisted edge type pruned on this sync (anti-phantom, full vocab)",
              pruned >= (set(FE.ALLOWED_EDGE_TYPES) - {"IMPORTS"}), str(sorted(pruned)))
        rev_prunes = [c for c, _ in log if "DELETE r" in c and "CASCADES" in c]
        check("CASCADES prunes are declaring-side-anchored",
              all("()-[r:CASCADES]->(a)" in c for c in rev_prunes) and rev_prunes, str(rev_prunes))


def gate_hook_payload_fuzz() -> None:
    print("\n-- notify-hook payload fuzz: 100 malformed payloads -> exit 0, root untouched --")
    import importlib.util
    spec = importlib.util.spec_from_file_location("dmrf_notify", HOOKS / "deepmap_sessionstart_notify.py")
    NT = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(NT)
    rng = random.Random(42)

    def rand_payload():
        r = rng.random()
        if r < 0.2:
            return ""
        if r < 0.4:
            return "".join(rng.choice(string.printable) for _ in range(rng.randint(1, 60)))
        d = {}
        for _ in range(rng.randint(0, 4)):
            d["".join(rng.choice("abcdefgh") for _ in range(4))] = rng.choice(
                [None, 1, "x", [], {}, "🚀", {"cwd": None}])
        if rng.random() < 0.5:
            d["cwd"] = rng.choice([None, 123, "", "Z:\\no\\such\\dir", []])
        if rng.random() < 0.5:
            d["session_id"] = rng.choice([None, "", 0, {"a": 1}])
        return json.dumps(d)

    def _state_snapshot(path: Path):
        try:
            st = path.stat()
            return (st.st_mtime_ns, st.st_size, path.read_bytes())
        except OSError:
            return None

    # A payload with no usable cwd falls back to the PROCESS cwd (resolve_root). run_all starts every suite
    # in the repo root, which is a provisioned DeepMap project on a developer box — so the fuzz used to
    # bump fake session ids into the developer's REAL <repo>/.deepmap/announce_state.json. The loop now
    # runs from an empty temp dir with DEEPMAP_PROJECT_ROOT unset (the cwd fallback is still fuzzed).
    real_state = REPO / ".deepmap" / "announce_state.json"
    before = _state_snapshot(real_state)
    failures = 0
    saved_cwd = os.getcwd()
    saved_root = os.environ.pop("DEEPMAP_PROJECT_ROOT", None)
    with tempfile.TemporaryDirectory() as th, tempfile.TemporaryDirectory() as tcwd:
        os.environ["DEEPMAP_HOME"] = str(th)
        saved_stdin, saved_stdout = sys.stdin, sys.stdout
        try:
            os.chdir(tcwd)
            for _ in range(100):
                sys.stdin = io.StringIO(rand_payload())
                sys.stdout = io.StringIO()
                try:
                    if NT.main() != 0:
                        failures += 1
                except Exception:
                    failures += 1
        finally:
            sys.stdin, sys.stdout = saved_stdin, saved_stdout
            os.chdir(saved_cwd)
            os.environ.pop("DEEPMAP_HOME", None)
            if saved_root is not None:
                os.environ["DEEPMAP_PROJECT_ROOT"] = saved_root
        stray = sorted(str(p.relative_to(tcwd)) for p in Path(tcwd).rglob("*"))
    check("100/100 malformed payloads -> exit 0, never raise", failures == 0, f"{failures} failed")
    check("the fuzz left the REAL repo's .deepmap/announce_state.json untouched",
          _state_snapshot(real_state) == before,
          f"{real_state} was {'modified' if before is not None else 'created'} by the fuzz loop")
    check("the fuzz wrote nothing into its (unprovisioned) temp cwd either", not stray, str(stray[:5]))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — Rails recognizer fuzz + metamorphic + money gates (no Neo4j)")
    print("=" * 64)
    import contextlib
    with contextlib.redirect_stderr(io.StringIO()):   # validator/skip noise stays out of CI logs
        gate_fuzz()
        gate_routes_fuzz()
        gate_metamorphic()
        gate_money()
        gate_hook_payload_fuzz()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
