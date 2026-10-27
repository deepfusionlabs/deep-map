#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gate: the setup-verdict census must mirror sync's vendor exclusion + see ERB/helpers/lib (F9). No Neo4j.

The 2026-07 field audit found two verdict-census defects:
  A) sync's default-on vendor filter drops `public/**/*.min.js` from the graph NODES, but the verdict
     census counted them anyway (its ignore set lacked `public` + the minified suffixes). So a Rails
     app with hundreds of vendored jQuery/ActiveScaffold bundles under public/ showed "N JavaScript
     files, ~11 nodes" -> density below the floor -> a PERMANENT false "LOW COVERAGE for JavaScript"
     DEGRADED, eroding trust in the honesty wedge.
  B) `.erb` was absent from the census language map and app/views/app/helpers/lib were not tracked
     conventional dirs, so the ERB views layer (a real, searchable :Module layer) could vanish from
     the graph with nothing flagging it.

Fix (pure, census-only — can only REMOVE a false DEGRADED, never introduce a false READY): add the
vendored dirs + minified suffixes sync already excludes to the census walk; map `.erb` to its OWN
'ERB' language (NOT Ruby, or unparsed templates dilute the Ruby bucket); add app/views/app/helpers/lib
to the tracked conventional dirs.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "build"))

import verdict as V  # noqa: E402

_PASS = _FAIL = 0


def check(desc: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    ok = bool(cond)
    _PASS += ok
    _FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc}" + ("" if ok else f"  -- {detail}"))


def _build_tree(root: Path) -> None:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "assets").mkdir(parents=True, exist_ok=True)
    (root / "public" / "javascripts").mkdir(parents=True, exist_ok=True)
    (root / "app" / "views" / "orders").mkdir(parents=True, exist_ok=True)
    (root / "app" / "views" / "shared").mkdir(parents=True, exist_ok=True)
    (root / "app" / "helpers").mkdir(parents=True, exist_ok=True)
    (root / "lib").mkdir(parents=True, exist_ok=True)
    # 2 REAL JavaScript files (the code the user wrote)
    (root / "src" / "app.js").write_text("export const x = 1;\n", encoding="utf-8")
    (root / "assets" / "main.js").write_text("function y(){}\n", encoding="utf-8")
    # 50 VENDORED minified bundles directly under public/ (jQuery/ActiveScaffold shape — NOT under a
    # dir the pre-fix ignore set already caught) -> tests the missing `public` dir component.
    for i in range(50):
        (root / "public" / "javascripts" / f"lib{i}.min.js").write_text("!function(){}();\n",
                                                                         encoding="utf-8")
    # 1 minified bundle OUTSIDE any vendored dir -> tests the *.min.js SUFFIX rule on its own.
    (root / "src" / "legacy.min.js").write_text("!function(){}();\n", encoding="utf-8")
    # 2 ERB view templates (a real searchable :Module layer)
    (root / "app" / "views" / "orders" / "index.html.erb").write_text("<%= 1 %>\n", encoding="utf-8")
    (root / "app" / "views" / "shared" / "_form.html.erb").write_text("<%= 2 %>\n", encoding="utf-8")
    # helpers + lib Ruby
    (root / "app" / "helpers" / "orders_helper.rb").write_text("module OrdersHelper\nend\n", encoding="utf-8")
    (root / "lib" / "utils.rb").write_text("module Utils\nend\n", encoding="utf-8")


def _nodes():
    """The graph nodes sync would have pushed: the 2 real JS, the 2 .erb :Module view nodes, the 2 Ruby
    files — the vendored public/*.min.js are NOT nodes (sync's vendor filter dropped them)."""
    return [
        {"name": "app", "label": "Module", "source_file": "src/app.js"},
        {"name": "y", "label": "Function", "source_file": "assets/main.js"},
        {"name": "orders/index", "label": "Module", "source_file": "app/views/orders/index.html.erb"},
        {"name": "shared/form", "label": "Module", "source_file": "app/views/shared/_form.html.erb"},
        {"name": "OrdersHelper", "label": "Module", "source_file": "app/helpers/orders_helper.rb"},
        {"name": "Utils", "label": "Module", "source_file": "lib/utils.rb"},
    ]


def gate_census_vendor_erb() -> None:
    print("\n-- F9: census excludes vendored public/*.min.js + counts .erb as ERB --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _build_tree(root)
        census = V.extension_census(root)
        check("50 vendored public/*.min.js are NOT counted as JavaScript (mirror sync's vendor filter)",
              census.get("JavaScript") == 2, str(census))
        check(".erb is counted as its OWN 'ERB' language (not diluting Ruby)",
              census.get("ERB") == 2, str(census))
        check(".rb under app/helpers + lib counts as Ruby (2), not polluted by .erb",
              census.get("Ruby") == 2, str(census))


def gate_no_false_js_degraded() -> None:
    print("\n-- F9: the vendored bundles no longer trip a false 'LOW COVERAGE for JavaScript' --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _build_tree(root)
        cov = V.build_coverage(root, _nodes())
        js = cov["languages"].get("JavaScript", {})
        check("coverage JS files == 2 (real only), nodes == 2 -> density 1.0",
              js.get("files") == 2 and js.get("nodes") == 2, str(js))
        erb = cov["languages"].get("ERB", {})
        check("coverage ERB files == 2, nodes == 2 (the view layer is visible to the census)",
              erb.get("files") == 2 and erb.get("nodes") == 2, str(erb))
        reasons = V._coverage_reasons(cov)
        check("NO false 'LOW COVERAGE for JavaScript' reason (the systematic false DEGRADED is gone)",
              not any("JavaScript" in r for r in reasons), str(reasons))
        # And classify() over the same facts is not DEGRADED-by-JS.
        v, rs = V.classify(neo4j_ok=True, n_nodes=len(_nodes()), n_embedded=len(_nodes()),
                           census=V.extension_census(root), extractor="graphify",
                           ml_ok=True, coverage=cov)
        check("classify() is not driven to DEGRADED by vendored JS",
              not any("JavaScript" in r for r in rs), f"{v}: {rs}")


def gate_conventional_dirs() -> None:
    print("\n-- F9: app/views, app/helpers, lib are tracked conventional dirs --")
    for d in ("app/views", "app/helpers", "lib"):
        check(f"{d} is a tracked conventional dir (the layer can no longer vanish silently)",
              d in V._CONVENTIONAL_DIRS, str(V._CONVENTIONAL_DIRS))
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _build_tree(root)
        cov = V.build_coverage(root, _nodes())
        check("app/views appears in the coverage dirs (2 erb files, 2 nodes)",
              cov["dirs"].get("app/views", {}).get("files") == 2, str(cov["dirs"]))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — verdict census vendor/ERB honesty gates (F9, no Neo4j)")
    print("=" * 64)
    gate_census_vendor_erb()
    gate_no_false_js_degraded()
    gate_conventional_dirs()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
