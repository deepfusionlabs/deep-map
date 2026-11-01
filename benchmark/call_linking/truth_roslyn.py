#!/usr/bin/env python3
"""C# call-site reference resolver for D3-cs (benchmark/call_linking/PREREG_CS.md §8.2, "The reference contract", as
its "Amendments before RC" AC1-AC5 and AC7 govern it): every invocation and object creation of a C# corpus, bound by
Roslyn 4.8.0's GetSymbolInfo over one CSharpCompilation per git-tracked .csproj, built as MSBuild builds it (AC3: the
transitive ProjectReference closure in dependency order, no Aspire resource reference, implicit usings from the
Directory.Build.props / Directory.Packages.props / .targets files MSBuild imports (AC21: the targets after the project,
the last assignment winning; every property value verbatim), System.Net.Http only outside .NETFramework, <Using Remove>
matched as MSBuild matches items, the generated InternalsVisibleTo attributes, the MAUI platform folders, the leftover
compilation; and, each measured against real MSBuild 17.9 / SDK 8.0.202: Exists() read on the tracked tree, MSBuild's
boolean-like comparison, Directory.Build.rsp's -p switches, TreatAsLocalProperty, an SDK named by an imported file,
<Using Update> and Exclude, %XX decoded after the ';' split, a trailing-period Remove, no global usings without an
SDK's Sdk.targets and no SDK list without its Sdk.props, UseWPF / UseWindowsForms, item types in any letter case,
<Choose>; Program.cs's header lists the rest it does not model), with the
runtime dispatch targets of each call (AC1: per implementing type, the override an object of that type runs; AC2: the
override rule's receivers). The .NET tool in truth_roslyn/ (Program.cs documents the contract rule by rule).

MEASUREMENT ONLY. Roslyn lives in the .NET tool next to this file (truth_roslyn/, restored OFFLINE from the local
global packages folder: its nuget.config clears every package source), never in a shipped list; nothing under build/
or hooks/ imports this module or names the tool (tests/call_linking_harness_gates.py A and G). Stdlib only at module
level.

    python benchmark/call_linking/truth_roslyn.py <corpus> <out.json> [--sites=<raw.jsonl>] [--threads=N] [--rebuild]
    python benchmark/call_linking/truth_roslyn.py --from-sites=<raw.jsonl> <out.json>
    python benchmark/call_linking/truth_roslyn.py selftest [--check] [--update] [--threads=N]
    python benchmark/call_linking/truth_roslyn.py identity [--truth=<out.json>]

The registered run passes NO option: the contract is the only mode (no option changes a row). --sites keeps the
tool's raw JSON lines; --from-sites converts such a file again without .NET (a pure function: byte-identical output);
--threads changes nothing in the output; --rebuild forces `dotnet build` (a build also happens whenever the dll's
stamp differs from the sources' sha256, which every output records as resolver.tool_sources_sha256); `identity`
prints PREREG_CS §1's reference-tool hash over AC7's file list (IDENTITY_FILES), and with --truth the versions that
run's resolver block records and whether its tool was built from these sources. The raw rows' diagnostics never reach
the truth rows: dispatch_ac1_added (the targets AC1 adds beyond FindImplementationForInterfaceMember's member) and
receiver_reading (AC2's reading of a type-parameter receiver) are counted in tool_counts. Every conversion checks
AC5 (contract_problems) and refuses a truth that breaks it. The corpus must be a git work tree: its files are
`git ls-files` (GIT_OPTIONAL_LOCKS=0; the tool only reads the corpus and writes nothing inside it).
Needs .NET SDK 8.0.202 (truth_roslyn/global.json; `dotnet` on PATH or DOTNET=<path>), the reference packs
Microsoft.NETCore.App.Ref 8.0.3 and Microsoft.AspNetCore.App.Ref 8.0.3, and Microsoft.CodeAnalysis.CSharp 4.8.0 with
its closure in the global packages folder.

The output is the kit's call-linking/truth/1 (truth_jedi.py's rows; _common.truth_pairs reads file, defstack, form,
name, defs) plus, per row, `dispatch`: entries of exactly the shape of the row's `defs` entries
    {file, line, alt_lines, type, name, full_name, interface, body}
  defs       the STATIC binding: one entry per declaration of the bound symbol in a compiled corpus file (an object
             creation: type "class", every declaration of a partial type; else type "function"); `interface` = the
             target is declared in an interface (G4c reads it), `body` = the declaration has a body
  dispatch   the runtime targets beyond the static binding (PREREG_CS §8.2 `dispatch`): the tool's dispatch_raw minus
             bodiless declarations, minus a declaration at a def's own location (S itself), and, for a caller that is
             not in a test file, minus test-file declarations (the product's rule, sync._is_test_path: replicated
             here as is_test_path and checked equal to the product's on every path the run touches; a disagreement
             refuses the run)
  plus site_kind, resolution, n_candidates, candidate_reason, project, dispatch_rule (interface | override, on every
  row with dispatch targets: AC5), dispatch_none, dispatch_dropped, receiver_type, target_kind, name_line, name_col,
  internals_visible_to ("generated" | "source" | null: whether the binding needed friend access into another corpus
  project, and whether an MSBuild-generated attribute (AC3) gave it).
Rows are sorted (file, line, col, name, name_line, name_col, site_kind); no timing and no absolute path, so a re-run
is byte-identical. The `resolver` block records the Roslyn and package versions, SDK, runtime, language version, the
reference packs (version, assembly count, sha256) and every project's evaluation (assembly name, SDK, first target
framework, preprocessor symbols, global usings, references, files, notes).

`selftest` materializes truth_roslyn/selftest/corpus/ (sources stored as <path>.txt) into a temporary directory, runs
the tool on it twice (one thread, then the default or --threads: the raw sites must be byte-identical), and requires
the raw sites and the truth to equal the captured selftest/sites.jsonl and selftest/truth.json byte for byte, and
every assertion of selftest/expect.json (hand-written from the contract and the amendments) to hold; `--check` does
the part that needs no .NET (the captured header's resolver.tool_sources_sha256 equals the current sources' sha256,
sites.jsonl -> truth.json, AC5, and expect.json); `--update` rewrites the captured files.

A reference resolver is not the truth: calls that need a NuGet package type or a source generator come out unresolved
or external, and code another configuration compiles is not read; the blind audit (audit_sample.py) decides
precision.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

KIT = Path(__file__).resolve().parent
TOOL = KIT / "truth_roslyn"
DLL = TOOL / "bin" / "Release" / "net8.0" / "TruthRoslyn.dll"
SOURCES = ("Program.cs", "TruthRoslyn.csproj", "nuget.config", "global.json")
STAMP = DLL.parent / "TruthRoslyn.sources.sha256"
SELFTEST = TOOL / "selftest"
WANT_ROSLYN = "4.8.0"
WANT_SDK = "8.0.202"
SITES_SCHEMA = "call-linking/roslyn-sites/2"
TRUTH_SCHEMA = "call-linking/truth/1"
ENTRY_KEYS = ("alt_lines", "body", "file", "full_name", "interface", "line", "name", "type")


# ---- the product's test-file rule, replicated (build/sync.py _is_test_path) ------------------------------------------
_TEST_DIR_PARTS = frozenset({"test", "tests", "__tests__", "spec", "specs", "testing", "e2e", "__mocks__"})
# The .NET test-project convention (build/sync.py _DOTNET_TEST_DIR_SUFFIXES): a DIRECTORY component whose lower-cased
# name ends with one of these (the dot is the anchor) is test code (src/Shop.Tests/FakeRepo.cs).
_DOTNET_TEST_DIR_SUFFIXES = (".tests", ".test", ".unittests", ".unittest", ".integrationtests", ".integrationtest",
                             ".functionaltests", ".acceptancetests", ".specs")


def is_test_path(source_file) -> bool:
    """build/sync.py `_is_test_path`, replicated line for line (the run refuses when the two disagree on any path it
    touches; the harness gate compares them on PROBE_PATHS)."""
    raw = str(source_file or "").replace("\\", "/")
    if not raw:
        return False
    parts = raw.split("/")
    for p in parts[:-1]:                                          # a DIRECTORY component is a test dir
        low = p.lower()
        if low in _TEST_DIR_PARTS or low.endswith(_DOTNET_TEST_DIR_SUFFIXES):
            return True
    name = parts[-1]
    lname = name.lower()
    if lname.startswith("test_") and lname.endswith(".py"):
        return True
    if lname.endswith(("_test.py", "_test.go", "_test.rs", "_spec.rb")):
        return True
    for e in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        if lname.endswith(".test" + e) or lname.endswith(".spec" + e):
            return True
    if name.endswith("Test.java") or name.endswith("Tests.java"):
        return True
    return False


# Every branch of the rule, both ways (the C# shapes of both corpora included), and the .NET test-project directory
# suffixes: each suffix, its case variants, and the production look-alikes the suffix rule must NOT match (a suffix
# without its dot, a suffix inside a name, a test-support library, a FILE named like a test project).
PROBE_PATHS = (
    "", "a.cs", "tests/A.cs", "Tests/A.cs", "src/tests/x/A.cs", "test/A.cs", "spec/A.cs", "specs/a.cs",
    "testing/a.cs", "e2e/a.cs", "__tests__/a.cs", "__mocks__/a.cs", "src/Jellyfin.Api.Tests/A.cs",
    "tests/Jellyfin.Api.Tests/Helpers/RequestHelpersTests.cs", "fuzz/Emby.Server.Implementations.Fuzz/Program.cs",
    "src/Ordering.API/Application/A.cs", "src/contest.cs", "tests.cs", "src/tests.cs", "test_a.py", "a_test.py",
    "a_test.go", "a_test.rs", "a_spec.rb", "a.test.ts", "a.spec.js", "a.test.mjs", "UserTest.java", "UserTests.java",
    "Latest.java", "USERTEST.JAVA", "a\\tests\\b.cs", "src/Test/A.cs", "src/TESTS/A.cs",
    # .NET test projects beside the code they test
    "src/Shop.Tests/X.cs", "src/Shop.UnitTests/X.cs", "src/Shop.Test/X.cs", "src/Shop.UnitTest/X.cs",
    "src/Shop.IntegrationTests/X.cs", "src/Shop.IntegrationTest/X.cs", "src/Shop.FunctionalTests/X.cs",
    "src/Shop.AcceptanceTests/X.cs", "src/Shop.Specs/X.cs", "Shop.Tests/Fakes/FakeRepo.cs", "src/SHOP.TESTS/X.cs",
    "src\\Shop.UnitTests\\X.cs", "Shop.Tests/FakeStore.cs",
    # production look-alikes
    "src/Contest/X.cs", "src/Latest.cs", "Latest.cs", "src/Shop.Latest/X.cs", "src/Shop.Contests/X.cs",
    "src/Shop.Testing.Abstractions/X.cs", "src/ShopTests/X.cs", "src/Shop.Tests.cs", "src/Shop.Tests.Support2/X.cs",
    "src/Shop.Spec/X.cs", "src/Attest/X.cs",
)

_PRODUCT_RULE = None


def product_test_rule():
    """The product's own rule (build/sync.py _is_test_path through _common), or None when it cannot be loaded."""
    global _PRODUCT_RULE
    if _PRODUCT_RULE is None:
        try:
            if str(KIT) not in sys.path:
                sys.path.insert(0, str(KIT))
            import _common  # noqa: WPS433 — lazy: the kit's one loader of the product rule
            _common.is_test_path("x.cs")
            _PRODUCT_RULE = _common.is_test_path
        except Exception as exc:  # noqa: BLE001
            print(f"[truth_roslyn] the product's test rule (build/sync.py _is_test_path) cannot be loaded: {exc!r}",
                  file=sys.stderr)
            _PRODUCT_RULE = False
    return _PRODUCT_RULE or None


def test_rule_disagreements(paths, product) -> list:
    """The paths (PROBE_PATHS included) on which the replica and the product's rule differ."""
    return sorted(p for p in set(PROBE_PATHS) | set(paths) if bool(is_test_path(p)) != bool(product(p)))


# ---- the derivations (pure: the harness gate checks them on the captured self-test) ---------------------------------
def entry(file, line, alt_lines, typ, name, full_name, interface, body) -> dict:
    """One defs / dispatch entry: the two lists share exactly this shape (ENTRY_KEYS)."""
    return {"file": file, "line": line, "alt_lines": [int(x) for x in alt_lines or []], "type": typ, "name": name,
            "full_name": full_name, "interface": bool(interface), "body": bool(body)}


def _key(e) -> tuple:
    return (e["file"], e["line"], e["type"], e["full_name"] or "", e["name"] or "")


def defs_of(row) -> list:
    """The static binding of one raw site: every in-corpus declaration of GetSymbolInfo(site).Symbol (a site bound to
    candidates only has none); an object creation binds the created type (type "class")."""
    if row.get("resolution") != "symbol":
        return []
    t = row.get("target") or {}
    typ = "class" if row.get("site_kind") == "object_creation" else "function"
    out, seen = [], set()
    for d in t.get("decls") or []:
        e = entry(d["file"], d["line"], d.get("alt_lines"), typ, t.get("name"), t.get("full_name"), t.get("interface"),
                  d.get("body"))
        k = (e["file"], e["line"], e["type"])
        if k not in seen:
            seen.add(k)
            out.append(e)
    return sorted(out, key=_key)


def dispatch_of(row, defs=None, is_test=is_test_path) -> "tuple[list, dict]":
    """(dispatch entries, dropped counts) of one raw site: dispatch_raw minus bodiless declarations, minus a
    declaration at a def's own location (S itself), and for a caller outside test files minus test-file targets."""
    if row.get("resolution") != "symbol":
        return [], {}
    defs = defs_of(row) if defs is None else defs
    own = {(d["file"], d["line"]) for d in defs}
    caller_test = bool(is_test(row.get("file")))
    out, seen, dropped = [], set(), Counter()
    for d in row.get("dispatch_raw") or []:
        if not d.get("body"):
            dropped["bodiless"] += 1
            continue
        if (d["file"], d["line"]) in own:
            dropped["static_target"] += 1
            continue
        if not caller_test and is_test(d["file"]):
            dropped["test_file"] += 1
            continue
        if (d["file"], d["line"]) in seen:
            continue
        seen.add((d["file"], d["line"]))
        out.append(entry(d["file"], d["line"], d.get("alt_lines"), "function", d.get("name"), d.get("full_name"),
                         d.get("interface"), d.get("body")))
    return sorted(out, key=_key), dict(sorted(dropped.items()))


def truth_row(row, is_test=is_test_path) -> dict:
    defs = defs_of(row)
    dispatch, dropped = dispatch_of(row, defs, is_test)
    out = {"file": row["file"], "line": row["line"], "col": row["col"], "form": row["form"], "name": row.get("name"),
           "name_line": row.get("name_line"), "name_col": row.get("name_col"), "defstack": row.get("defstack") or [],
           "defs": defs, "dispatch": dispatch, "dispatch_dropped": dropped, "site_kind": row.get("site_kind"),
           "resolution": row.get("resolution"), "n_candidates": row.get("n_candidates", 0),
           "candidate_reason": row.get("candidate_reason"), "project": row.get("project"),
           "dispatch_rule": row.get("dispatch_rule"), "dispatch_none": row.get("dispatch_none"),
           "receiver_type": row.get("receiver_type"), "target_kind": (row.get("target") or {}).get("kind"),
           "internals_visible_to": row.get("internals_visible_to")}
    if row.get("error"):
        out["error"] = row["error"]
    return out


DISPATCH_RULES = ("interface", "override")


def contract_problems(doc) -> list:
    """PREREG_CS AC5 on a truth document (every conversion checks it; the run refuses on any): every row carries
    `dispatch` (a list) and `defs` (a list); a row with dispatch targets carries `dispatch_rule` interface|override; every
    defs and dispatch entry has exactly ENTRY_KEYS, with `interface` and `body` booleans."""
    out = []
    for r in doc.get("rows") or []:
        where = f"{r.get('file')}:{r.get('line')}:{r.get('col')} {r.get('name')!r}"
        if not isinstance(r.get("dispatch"), list) or not isinstance(r.get("defs"), list):
            out.append(f"{where}: defs / dispatch not lists")
            continue
        if r["dispatch"] and r.get("dispatch_rule") not in DISPATCH_RULES:
            out.append(f"{where}: dispatch targets without dispatch_rule interface|override ({r.get('dispatch_rule')!r})")
        for e in r["defs"] + r["dispatch"]:
            if not isinstance(e, dict) or set(e) != set(ENTRY_KEYS):
                out.append(f"{where}: entry keys {sorted(e) if isinstance(e, dict) else type(e).__name__}")
            elif not isinstance(e["interface"], bool) or not isinstance(e["body"], bool):
                out.append(f"{where}: entry interface / body not booleans")
    return out


def _row_key(r) -> tuple:
    return (r["file"], r["line"], r["col"], r.get("name") or "", r.get("name_line") or 0, r.get("name_col") or 0,
            r.get("site_kind") or "")


def touched_paths(rows) -> list:
    """Every file path whose test classification a conversion reads."""
    out = set()
    for r in rows:
        out.add(r.get("file") or "")
        for d in r.get("dispatch_raw") or []:
            out.add(d.get("file") or "")
    return sorted(out)


def to_truth(header, rows, is_test=is_test_path, test_rule=None) -> dict:
    """The kit's truth document from the tool's header and raw rows (pure)."""
    out_rows = sorted((truth_row(r, is_test) for r in rows), key=_row_key)
    counts = Counter()
    for r in out_rows:
        counts["sites"] += 1
        counts["site_kind:" + str(r["site_kind"])] += 1
        counts["form:" + str(r["form"])] += 1
        counts["resolution:" + str(r["resolution"])] += 1
        counts["rows_with_defs"] += bool(r["defs"])
        counts["bound_external"] += r["resolution"] == "symbol" and not r["defs"]
        counts["unresolved"] += r["resolution"] != "symbol"
        counts["rows_with_dispatch"] += bool(r["dispatch"])
        counts["dispatch_entries"] += len(r["dispatch"])
        if r["dispatch"]:
            counts["dispatch_rows:" + str(r["dispatch_rule"])] += 1
            counts["dispatch_entries:" + str(r["dispatch_rule"])] += len(r["dispatch"])
        for k, v in r["dispatch_dropped"].items():
            counts["dispatch_dropped:" + k] += v
        if r.get("internals_visible_to"):
            counts["internals_visible_to:" + r["internals_visible_to"]] += 1
        counts["resolver_errors"] += bool(r.get("error"))
    hc = header.get("counts") or {}
    res = dict(header.get("resolver") or {})
    res.update({"contract": header.get("contract"), "packs": header.get("packs"), "projects": header.get("projects"),
                "leftover": header.get("leftover"), "retargeted_references": header.get("retargeted_references"),
                "test_rule": test_rule, "options": {}})
    perr = header.get("parse_errors") or []
    return {"schema": TRUTH_SCHEMA, "resolver": res, "corpus": header.get("corpus"), "n_files": header.get("n_files"),
            "n_source_types": header.get("n_source_types"), "n_parse_errors": len(perr),
            "parse_errors": sorted(f"{p[0]}: {p[1]} syntax errors" for p in perr), "parse_error_detail": perr,
            "uncompiled_files": header.get("uncompiled_files") or [], "shared_files": header.get("shared_files") or [],
            "xml_errors": header.get("xml_errors") or {}, "n_resolver_errors": counts["resolver_errors"],
            "n_nameof_skipped": hc.get("nameof_skipped", 0), "n_candidates_only": counts["resolution:candidates"],
            "counts": dict(sorted(counts.items())), "tool_counts": hc, "rows": out_rows}


def dumps(doc) -> str:
    """The kit's one serialisation (_common.dump_json): sorted keys, compact, UTF-8, one trailing LF."""
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def write_text(path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def read_sites(path) -> "tuple[dict, list]":
    with open(path, encoding="utf-8", newline="") as fh:
        first = fh.readline()
        header = json.loads(first) if first.strip() else {}
        rows = [json.loads(line) for line in fh if line.strip()]
    if header.get("schema") != SITES_SCHEMA or header.get("kind") != "header":
        raise ValueError(f"{path}: not a truth_roslyn sites file (schema {header.get('schema')!r}, want {SITES_SCHEMA})")
    return header, rows


def convert(header, rows, product=None) -> "tuple[dict | None, str | None]":
    """The checked conversion every CLI path runs: Roslyn 4.8.0 only, and the replicated test rule equal to the
    product's on every touched path (else (None, why))."""
    res = header.get("resolver") or {}
    for k in ("microsoft_codeanalysis", "microsoft_codeanalysis_csharp"):
        if not str(res.get(k) or "").startswith(WANT_ROSLYN):
            return None, f"Roslyn {res.get(k)!r} ({k}); the registered reference is {WANT_ROSLYN}"
    product = product or product_test_rule()
    if product is None:
        return None, "the product's test rule (build/sync.py _is_test_path) cannot be loaded to check the replica"
    paths = touched_paths(rows)
    bad = test_rule_disagreements(paths, product)
    if bad:
        return None, f"the replicated test rule differs from build/sync.py _is_test_path on {len(bad)} path(s): {bad[:10]}"
    rule = {"rule": "build/sync.py _is_test_path, replicated as truth_roslyn.is_test_path",
            "checked_paths": len(set(paths) | set(PROBE_PATHS)), "agree": True}
    doc = to_truth(header, rows, is_test_path, rule)
    bad = contract_problems(doc)
    if bad:
        return None, f"the truth breaks PREREG_CS AC5 on {len(bad)} row(s): {bad[:5]}"
    return doc, None


# ---- running the tool -------------------------------------------------------------------------------------------------
def _env() -> dict:
    e = dict(os.environ)
    e.update({"DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1", "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
              "DOTNET_CLI_UI_LANGUAGE": "en", "MSBUILDDISABLENODEREUSE": "1", "DOTNET_GENERATE_ASPNET_CERTIFICATE":
              "false", "DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE": "1", "NUGET_XMLDOC_MODE": "skip"})
    return e


def dotnet_exe() -> "str | None":
    return os.environ.get("DOTNET") or shutil.which("dotnet")


def sdk_version(dotnet: str) -> "str | None":
    """The SDK `dotnet` picks inside the tool directory (its global.json applies)."""
    try:
        p = subprocess.run([dotnet, "--version"], cwd=str(TOOL), env=_env(), capture_output=True, text=True,
                           timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    return (p.stdout.strip() or None) if p.returncode == 0 else None


def sources_sha256() -> str:
    """sha256 over the tool's build inputs (truth_roslyn/<SOURCES>, name order; name, NUL, bytes, NUL each)."""
    h = hashlib.sha256()
    for s in sorted(SOURCES):
        h.update(s.encode("utf-8") + b"\0")
        h.update((TOOL / s).read_bytes() + b"\0")
    return h.hexdigest()


def needs_build() -> bool:
    """A build is needed unless the dll exists and was built from exactly the current sources (a content stamp, not
    mtimes: a copy that keeps old mtimes never runs a stale tool)."""
    if not DLL.is_file() or not STAMP.is_file():
        return True
    return STAMP.read_text(encoding="ascii").strip() != sources_sha256()


IDENTITY_FILES = ("truth_roslyn.py",) + tuple(f"truth_roslyn/{s}" for s in SOURCES)
RESOLVER_VERSION_KEYS = ("microsoft_codeanalysis", "microsoft_codeanalysis_csharp", "packages", "sdk", "runtime",
                         "language_version", "tool_sources_sha256")


def tool_identity(truth_doc=None) -> dict:
    """PREREG_CS §1 "Reference tool identity" as AC7 names it: sha256 over truth_roslyn.py and
    truth_roslyn/{Program.cs, TruthRoslyn.csproj, global.json, nuget.config} (IDENTITY_FILES), in byte order of their
    forward-slash paths relative to benchmark/call_linking/, each contributing its path, a NUL, its bytes and a NUL
    (the kit-hash scheme of §1). With a truth document (`identity --truth=<out.json>`), the versions its resolver
    block prints (packages, SDK, runtime, language version) are added, and whether that run's tool was built from
    these sources."""
    files = sorted(IDENTITY_FILES, key=lambda p: p.encode("utf-8"))
    h = hashlib.sha256()
    for rel in files:
        h.update(rel.encode("utf-8") + b"\0")
        h.update((KIT / rel).read_bytes() + b"\0")
    out = {"sha256": h.hexdigest(), "files": files, "tool_sources_sha256": sources_sha256()}
    if truth_doc is not None:
        res = truth_doc.get("resolver") or {}
        out["resolver"] = {k: res.get(k) for k in RESOLVER_VERSION_KEYS}
        out["run_built_from_these_sources"] = res.get("tool_sources_sha256") == out["tool_sources_sha256"]
    return out


def build(dotnet: str) -> int:
    """`dotnet build -c Release` of the tool (offline: nuget.config has no package source); stamps the dll with the
    sources' sha256."""
    stamp = sources_sha256()
    cmd = [dotnet, "build", str(TOOL / "TruthRoslyn.csproj"), "-c", "Release", "--nologo", "-v", "q",
           "-nodeReuse:false", "-p:UseSharedCompilation=false"]
    print(f"[truth_roslyn] building the reference tool: {' '.join(cmd)}", file=sys.stderr)
    p = subprocess.run(cmd, cwd=str(TOOL), env=_env(), capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if p.returncode != 0 or not DLL.is_file():
        print(p.stdout[-4000:], file=sys.stderr)
        print(p.stderr[-4000:], file=sys.stderr)
        print("[truth_roslyn] the build FAILED (an NU1101 means a package of the Roslyn 4.8.0 closure is not in the "
              "global packages folder: the restore is offline by design)", file=sys.stderr)
        return p.returncode or 1
    with open(STAMP, "w", encoding="ascii", newline="\n") as fh:
        fh.write(stamp + "\n")
    return 0


def _git(corpus: Path, *args) -> "subprocess.CompletedProcess":
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return subprocess.run(["git", "-C", str(corpus), *args], env=env, capture_output=True)


def git_tracked(corpus: Path) -> "tuple[list | None, str | None, str]":
    """(tracked paths, HEAD, why-not): `git ls-files -z` of the corpus (paths relative to it)."""
    p = _git(corpus, "ls-files", "-z", "--cached")
    if p.returncode != 0:
        return None, None, (p.stderr or b"").decode("utf-8", "replace").strip() or "git ls-files failed"
    files = sorted({x for x in p.stdout.decode("utf-8").split("\0") if x})
    h = _git(corpus, "rev-parse", "HEAD")
    head = h.stdout.decode("ascii", "replace").strip() if h.returncode == 0 else None
    return files, head, ""


def prepare_tool(rebuild=False) -> "tuple[str | None, str | None, int]":
    """(dotnet, sdk, rc): the tool built and the SDK the registered contract names."""
    dotnet = dotnet_exe()
    if not dotnet:
        print("[truth_roslyn] no `dotnet` on PATH (set DOTNET=<path to dotnet>): the reference tool needs .NET SDK "
              f"{WANT_SDK}", file=sys.stderr)
        return None, None, 2
    sdk = sdk_version(dotnet)
    if sdk != WANT_SDK:
        print(f"[truth_roslyn] REFUSED: the SDK in {TOOL} is {sdk!r}; the registered reference is built with .NET SDK "
              f"{WANT_SDK}", file=sys.stderr)
        return None, None, 2
    if rebuild or needs_build():
        rc = build(dotnet)
        if rc:
            return None, None, rc
    return dotnet, sdk, 0


def run_tool(dotnet, sdk, corpus: Path, sites: Path, files, file_list: str, head, threads=None) -> int:
    """The tool over `corpus` with `files` as its tracked list; raw JSON lines to `sites`."""
    tmp = Path(tempfile.mkdtemp(prefix="truth_roslyn_list_"))
    lst = tmp / "tracked.lst"
    try:
        with open(lst, "w", encoding="utf-8", newline="") as fh:
            fh.write("\0".join(files))
        cmd = [dotnet, str(DLL), str(corpus), str(sites), f"--tracked={lst}", f"--file-list={file_list}",
               f"--record-sdk={sdk}", f"--record-tool={STAMP.read_text(encoding='ascii').strip()}"] + \
              ([f"--record-head={head}"] if head else []) + ([f"--threads={threads}"] if threads else [])
        p = subprocess.run(cmd, env=_env())
        if p.returncode != 0:
            print(f"[truth_roslyn] the reference tool exited {p.returncode}", file=sys.stderr)
        return p.returncode
    finally:
        if lst.is_file():
            lst.unlink()
        tmp.rmdir()


# ---- the self-test ----------------------------------------------------------------------------------------------------
def selftest_sources() -> list:
    """(relative path, text) of every self-test corpus file (selftest/corpus/<path>.txt), LF, byte-ordered."""
    root = SELFTEST / "corpus"
    out = []
    for p in sorted(root.rglob("*.txt")):
        rel = p.relative_to(root).as_posix()[:-4]
        out.append((rel, p.read_bytes().decode("utf-8").replace("\r\n", "\n")))
    return sorted(out)


def materialize(dest: Path, files: list, dirs: list) -> None:
    """Write the self-test corpus under `dest` (an empty directory the caller created), appending every file and
    directory it creates to `files` / `dirs` as it goes, so the caller deletes exactly those, even after a failure."""
    for rel, text in selftest_sources():
        p = dest / rel
        missing = []
        d = p.parent
        while d != dest and not d.exists():
            missing.append(d)
            d = d.parent
        for d in reversed(missing):
            d.mkdir()
            dirs.append(d)
        files.append(p)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)


def _read_lf(path: Path) -> "bytes | None":
    return path.read_bytes().replace(b"\r\n", b"\n") if path.is_file() else None


def _rows_at(truth, a) -> list:
    """The rows a selector picks: file and line, plus name / site_kind / col when it gives them."""
    return [r for r in truth["rows"] if r["file"] == a["file"] and r["line"] == a["line"]
            and all(r.get(k) == a[k] for k in ("name", "site_kind", "col") if k in a)]


def _locs(entries) -> list:
    return sorted([e["file"], e["line"]] for e in entries)


def expect_problems(truth, expect) -> list:
    """Every selftest/expect.json assertion that does not hold on `truth`. A site assertion selects the rows at
    (file, line[, name][, site_kind][, col]) and requires exactly one, then compares each given field: form, defstack,
    defs / dispatch ([[file, line], ...]), def_alt_lines / def_types / def_interface / def_body (one value per def, in
    the row's order), resolution, n_candidates, candidate_reason, project, dispatch_rule, dispatch_none,
    dispatch_dropped, receiver_type, name_line, internals_visible_to. `absent_sites` requires no row at
    [file, line(, name)]; `absent` no row in a file; `file_project` the project every row of a file comes from; `doc`
    compares a path of the truth document ("a|b|c"; a list step picks the entry whose "path" is that step)."""
    out = []
    for i, a in enumerate(expect.get("sites") or []):
        what = a.get("what") or f"site #{i}"
        got = _rows_at(truth, a)
        if len(got) != 1:
            out.append(f"{what}: {len(got)} rows at {a['file']}:{a['line']} {a.get('name')!r}")
            continue
        r = got[0]
        for k in ("form", "defstack", "resolution", "n_candidates", "candidate_reason", "project", "dispatch_rule",
                  "dispatch_none", "dispatch_dropped", "receiver_type", "name_line", "internals_visible_to"):
            if k in a and r.get(k) != a[k]:
                out.append(f"{what}: {k} {r.get(k)!r} != {a[k]!r}")
        for k in ("defs", "dispatch"):
            if k in a and _locs(r[k]) != sorted(a[k]):
                out.append(f"{what}: {k} {_locs(r[k])} != {sorted(a[k])}")
        for k, field in (("def_alt_lines", "alt_lines"), ("def_types", "type"), ("def_interface", "interface"),
                         ("def_body", "body")):
            if k in a and [d[field] for d in r["defs"]] != a[k]:
                out.append(f"{what}: {k} {[d[field] for d in r['defs']]} != {a[k]}")
    for a in expect.get("absent_sites") or []:
        sel = {"file": a[0], "line": a[1]}
        if len(a) > 2:
            sel["name"] = a[2]
        if _rows_at(truth, sel):
            out.append(f"absent_sites: a row at {a}")
    for f in expect.get("absent") or []:
        n = sum(1 for r in truth["rows"] if r["file"] == f)
        if n:
            out.append(f"absent: {f} has {n} rows")
    for f, proj in sorted((expect.get("file_project") or {}).items()):
        got = sorted({r["project"] for r in truth["rows"] if r["file"] == f})
        if got != [proj]:
            out.append(f"file_project: {f} rows from {got}, want [{proj!r}]")
    for path, want in sorted((expect.get("doc") or {}).items()):
        cur = truth
        for part in path.split("|"):
            if isinstance(cur, list):
                cur = next((x for x in cur if isinstance(x, dict) and x.get("path") == part), None)
            elif isinstance(cur, dict):
                cur = cur.get(part)
            else:
                cur = None
        if cur != want:
            out.append(f"doc {path}: {cur!r} != {want!r}")
    return out


def check_selftest() -> list:
    """The self-test's checks that need no .NET: the captured raw sites were made by the tool built from the CURRENT
    sources (the header's resolver.tool_sources_sha256 == sources_sha256(), so an edit of Program.cs, the csproj,
    global.json or nuget.config that was not re-captured fails here, on a machine without .NET too), they convert to
    the captured truth byte for byte (with the replicated test rule, PREREG_CS AC5 checked), and every expect.json
    assertion holds on it. Returns the problems ([] = pass)."""
    out = []
    sites, want, exp = SELFTEST / "sites.jsonl", SELFTEST / "truth.json", SELFTEST / "expect.json"
    for p in (sites, want, exp):
        if not p.is_file():
            out.append(f"missing {p.relative_to(KIT).as_posix()}")
    if out:
        return out
    header, rows = read_sites(sites)
    recorded = (header.get("resolver") or {}).get("tool_sources_sha256")
    if recorded != sources_sha256():
        out.append(f"selftest/sites.jsonl was captured by the tool built from other sources (its "
                   f"resolver.tool_sources_sha256 {recorded!r}, the current sources' {sources_sha256()}): re-capture "
                   f"with `truth_roslyn.py selftest --update`")
    doc, why = convert(header, rows)
    if doc is None:
        return out + [f"conversion refused: {why}"]
    got = dumps(doc).encode("utf-8")
    if got != _read_lf(want):
        out.append("selftest/sites.jsonl does not convert to selftest/truth.json byte for byte")
    with open(exp, encoding="utf-8") as fh:
        out.extend(expect_problems(doc, json.load(fh)))
    return out


def selftest(update=False, check_only=False, threads=None) -> int:
    if check_only:
        problems = check_selftest()
        for p in problems:
            print(f"  [FAIL] {p}", file=sys.stderr)
        print(f"[truth_roslyn] selftest --check: {'PASS' if not problems else f'{len(problems)} problem(s)'}",
              file=sys.stderr)
        return 1 if problems else 0
    dotnet, sdk, rc = prepare_tool()
    if rc:
        return rc
    work = Path(tempfile.mkdtemp(prefix="truth_roslyn_selftest_"))
    corpus = work / "corpus"
    corpus.mkdir()
    sites, sites1 = work / "sites.jsonl", work / "sites.t1.jsonl"
    created_files, created_dirs = [], []
    try:
        materialize(corpus, created_files, created_dirs)
        files = sorted(p.relative_to(corpus).as_posix() for p in created_files)
        # twice: one thread (the files in byte order) and the default / --threads (racing): byte-identical, so no
        # result depends on the order the files reach a shared cache
        rc = run_tool(dotnet, sdk, corpus, sites1, files, "listed", None, 1) or \
            run_tool(dotnet, sdk, corpus, sites, files, "listed", None, threads)
        if rc:
            return rc
        raw = _read_lf(sites)
        same_threads = raw == _read_lf(sites1)
        header, rows = read_sites(sites)
        doc, why = convert(header, rows)
        if doc is None:
            print(f"[truth_roslyn] REFUSED: {why}", file=sys.stderr)
            return 2
        text = dumps(doc)
        if update and same_threads:
            write_text(SELFTEST / "sites.jsonl", raw.decode("utf-8"))
            write_text(SELFTEST / "truth.json", text)
            print("[truth_roslyn] selftest: captured sites.jsonl and truth.json rewritten", file=sys.stderr)
        problems = [] if same_threads else ["the tool's raw sites differ between --threads=1 and "
                                            f"--threads={threads or 'default'} (nothing was captured)"]
        if raw != _read_lf(SELFTEST / "sites.jsonl"):
            problems.append("the tool's raw sites differ from selftest/sites.jsonl")
        if text.encode("utf-8") != _read_lf(SELFTEST / "truth.json"):
            problems.append("the truth differs from selftest/truth.json")
        problems.extend(check_selftest())
        for p in problems:
            print(f"  [FAIL] {p}", file=sys.stderr)
        print(f"[truth_roslyn] selftest: {'PASS' if not problems else f'{len(problems)} problem(s)'} "
              f"({len(rows)} sites)", file=sys.stderr)
        return 1 if problems else 0
    finally:                                     # exactly the paths this run created
        for s_ in (sites, sites1):
            if s_.is_file():
                s_.unlink()
        for p in created_files:
            if p.is_file():
                p.unlink()
        for d in reversed(created_dirs):
            if d.is_dir():
                d.rmdir()
        corpus.rmdir()
        work.rmdir()


# ---- the CLI ----------------------------------------------------------------------------------------------------------
def _parse(argv):
    opts = {"pos": [], "sites": None, "from_sites": None, "rebuild": False, "threads": None, "update": False,
            "check": False, "truth": None}
    for a in argv:
        k, _, v = a.partition("=")
        if a in ("-h", "--help"):
            return None
        if not a.startswith("--"):
            opts["pos"].append(a)
        elif k == "--sites" and v:
            opts["sites"] = v
        elif k == "--truth" and v:
            opts["truth"] = v
        elif k == "--from-sites" and v:
            opts["from_sites"] = v
        elif a == "--rebuild":
            opts["rebuild"] = True
        elif k == "--threads" and v.isdigit() and int(v) > 0:
            opts["threads"] = int(v)
        elif a == "--update":
            opts["update"] = True
        elif a == "--check":
            opts["check"] = True
        else:
            print(f"[truth_roslyn] unknown option {a}", file=sys.stderr)
            return None
    return opts


def main(argv=None) -> int:
    for s_ in (sys.stdout, sys.stderr):
        try:
            s_.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    opts = _parse(list(sys.argv[1:] if argv is None else argv))
    if opts is None:
        print(__doc__, file=sys.stderr)
        return 2
    pos = opts["pos"]
    if pos[:1] == ["selftest"] and len(pos) == 1:
        return selftest(update=opts["update"], check_only=opts["check"], threads=opts["threads"])
    if pos[:1] == ["identity"] and len(pos) == 1:
        doc = None
        if opts["truth"]:
            try:
                with open(opts["truth"], encoding="utf-8") as fh:
                    doc = json.load(fh)
            except (OSError, ValueError) as exc:
                print(f"[truth_roslyn] cannot read the truth file: {exc}", file=sys.stderr)
                return 2
        print(dumps(tool_identity(doc)), end="")
        return 0
    t0 = time.time()
    if opts["from_sites"]:
        if len(pos) != 1:
            print(__doc__, file=sys.stderr)
            return 2
        out = Path(pos[0])
        try:
            header, rows = read_sites(opts["from_sites"])
        except (OSError, ValueError) as exc:
            print(f"[truth_roslyn] cannot read the sites file: {exc}", file=sys.stderr)
            return 2
    else:
        if len(pos) != 2:
            print(__doc__, file=sys.stderr)
            return 2
        corpus, out = Path(pos[0]).resolve(), Path(pos[1])
        files, head, why = git_tracked(corpus)
        if files is None:
            print(f"[truth_roslyn] REFUSED: {corpus} is not a git work tree the reference can list ({why}); the "
                  f"contract reads git-tracked files", file=sys.stderr)
            return 2
        dotnet, sdk, rc = prepare_tool(opts["rebuild"])
        if rc:
            return rc
        tmpdir = None
        if opts["sites"]:
            sites = Path(opts["sites"])
        else:
            tmpdir = Path(tempfile.mkdtemp(prefix="truth_roslyn_"))
            sites = tmpdir / "sites.jsonl"
        try:
            rc = run_tool(dotnet, sdk, corpus, sites, files, "git", head, opts["threads"])
            if rc:
                return rc
            header, rows = read_sites(sites)
        finally:
            if tmpdir is not None:              # only the two paths this run created
                if sites.is_file():
                    sites.unlink()
                tmpdir.rmdir()
    doc, why = convert(header, rows)
    if doc is None:
        print(f"[truth_roslyn] REFUSED: {why}", file=sys.stderr)
        return 2
    write_text(out, dumps(doc))
    c, tc = doc["counts"], doc["tool_counts"]
    by_rule = ", ".join(f"{r} {c.get('dispatch_rows:' + r, 0)} rows / {c.get('dispatch_entries:' + r, 0)} entries"
                        for r in DISPATCH_RULES)
    print(f"[truth_roslyn] files {doc['n_files']} (syntax-error files {doc['n_parse_errors']}, uncompiled "
          f"{len(doc['uncompiled_files'])}, MAUI platform files excluded {tc.get('maui_platform_files_excluded', 0)}) "
          f"sites {c.get('sites', 0)}: bound in corpus {c.get('rows_with_defs', 0)}, external "
          f"{c.get('bound_external', 0)}, unresolved {c.get('unresolved', 0)} (candidates only "
          f"{doc['n_candidates_only']}); dispatch {by_rule}; InternalsVisibleTo-unlocked "
          f"{tc.get('ivt_sites:generated', 0)} (generated) + {tc.get('ivt_sites:source', 0)} (source); resolver "
          f"errors {doc['n_resolver_errors']} in {time.time() - t0:.1f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
