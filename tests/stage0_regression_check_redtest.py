#!/usr/bin/env python3
"""stage0_regression_check_redtest.py — proves the gate is LOAD-BEARING.  [NEO4J]

Monkeypatches a COPY of the retriever (NEVER build/retrieval.py on disk) and asserts
stage0_regression_check FAILS (exit 1). If the gate stays green under the mutation,
the gate is decorative — the false confidence STAGE 0 exists to kill (plan §2.4; :362).

Requires a live graph built from the oracle's pinned corpus + the frozen oracle (same precondition as
the gate). Every argument is forwarded to the gate, which never defaults its target, e.g.:
    RETRIEVER_MODULE=build/retrieval.py python tests/stage0_regression_check_redtest.py
        --repo-id self --project <the throwaway dir from `tests/stage0_refreeze.py --check --keep`>
A gate exit 2 (no target / wrong graph / harness error) is NOT a pass: only exit 1 proves the gate RED.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_rm = os.environ.get("RETRIEVER_MODULE")
if not _rm:
    sys.exit("FATAL: set RETRIEVER_MODULE from T0 (A/B) — do not default.")
RETRIEVER_MODULE = Path(_rm).resolve()

src = RETRIEVER_MODULE.read_text(encoding="utf-8")

# Choose the mutation: a guaranteed-RED structural flip (fixture-independent), with the
# semantic weight-halving available as an alternative anchor.
GUARANTEED = ("reverse=True", "reverse=False")        # final fused sort, retrieval.py:137
SEMANTIC   = ('    "vector": 1.0,',
              '    "vector": 0.5,   # RED-MUTATION (copy only) — breaks equal-weight==RRF')

mutated = src.replace(GUARANTEED[0], GUARANTEED[1], 1)
if mutated == src:
    mutated = src.replace(SEMANTIC[0], SEMANTIC[1], 1)
assert mutated != src, "mutation anchor not found — adjust to current retrieval.py (line 137 / line 66)."

tmpdir = Path(tempfile.mkdtemp(prefix="deepmap_redtest_"))
mutated_path = tmpdir / "retrieval_MUTATED.py"
mutated_path.write_text(mutated, encoding="utf-8")

env = dict(os.environ)
env["RETRIEVER_MODULE"] = str(mutated_path)           # point the gate at the COPY

result = subprocess.run(
    [sys.executable, str(Path(__file__).resolve().parent / "stage0_regression_check.py"), *sys.argv[1:]],
    env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
)

print("--- gate stderr under mutation ---")
print(result.stderr)

if result.returncode == 1:
    print("[redtest] PASS: gate correctly went RED under the mutation. The gate is load-bearing.")
    sys.exit(0)
else:
    print(f"[redtest] FAIL: gate returned {result.returncode}, expected 1. "
          f"The gate is DECORATIVE — it did not catch the regression.", file=sys.stderr)
    sys.exit(1)
