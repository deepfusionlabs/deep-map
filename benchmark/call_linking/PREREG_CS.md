# Call linking, C#: pre-registration of decision D3-cs (PREREG_CS)

Registered 2026-10-28, before any DeepMap data exists for either C# corpus. No DeepMap sync, extraction,
typed-linking run or measurement has touched jellyfin or dotnet/eShop. This file registers ONE decision, D3-cs:
whether C# typed-receiver call linking (`DEEPMAP_TYPED_CALLS=csharp`) becomes part of the default graph.

It follows the rule PREREG.md set for every wave-2 language ("Each gets its own D3 against its own reference, plus R1
on any benchmark questions in that language", PREREG.md §8.5, with "C# via Roslyn `GetSymbolInfo`" as the reference,
§8.2) and DECISION.md's "Any route forward is a new registration". PREREG.md, PRE_RC_DECISIONS.md, RESULTS.md and
DECISION.md are closed. This file edits none of them and uses none of their data; it cites their rules where it
applies them.

Rules:
- **Thresholds are never edited after data.**
- **Amendments only before the first RC run** (§8.1 defines it). Each is dated, gives its reason and is recorded in
  "Amendments before RC" (just above the protocol block). The block itself stays as registered; an amendment
  governs the protocol items it names.
- **The protocol block is hash-locked.** It is everything strictly between the line starting
  `<!-- PREREG-CS-PROTOCOL BEGIN` and the line starting `<!-- PREREG-CS-PROTOCOL END`. `tests/call_linking_harness_gates.py`
  locks its sha256 the way its section H locks PREREG §8: those lines joined with LF, plus one final LF. The lock
  lands before the first RC run.
- A change after the first RC run is recorded in DECISION_CS.md with its reason, never in this file.
- RESULTS_CS.md and DECISION_CS.md (in this folder) are written once, against this file.
- No pilot number exists. Any DeepMap number made before the first RC run is PILOT, labelled so, and never decision
  data.

## 1. Release-candidate code identity

Filled once, before the first RC run. Filling these slots is not an amendment; nothing else in this section changes.

- **RC code:** a frozen snapshot of the working tree as of the RC suite run, copied (build/, hooks/, tests/,
  benchmark/call_linking/) into a scratch directory outside this repository and never edited. There is no commit:
  the operator commits, as for PREREG.md §1 ("the working tree as of the S5 suite run"). The RC is that snapshot,
  never the moving working tree.
- **Content hash of build/\*.py:** `<to be filled>`, the output of
  `python benchmark/call_linking/offline_calls.py codehash --build <snapshot>/build` (PREREG §1's scheme: sha256
  over build/*.py in name order, each file contributing its name, a NUL, its bytes and a NUL; plus the file count).
  Every RC dump stamps the same hash in its `code` field; a dump with another hash is not RC data.
- **Kit hash:** `<to be filled>`: sha256 over every file of benchmark/call_linking/ in the snapshot (truth_roslyn/
  included; `__pycache__/`, build outputs, RESULTS_CS.md, DECISION_CS.md and `cs/` excluded), in byte order of their
  forward-slash relative paths, each contributing its relative path, a NUL, its bytes and a NUL.
- **Reference tool identity:** `<to be filled>`: the same hash over truth_roslyn.py and truth_roslyn/'s sources
  alone, plus the restored package versions and the SDK and runtime versions the tool prints in its `resolver` block.
- The kit and the reference tool as of that snapshot measure it. A tool change after the first RC run voids the runs
  it touches. DECISION.md ruling 1 applies unchanged: a dump never carries `call_linking.seconds`; the timing sidecar
  does.

## 2. Engine builds and reference resolver

Fixed in §8.2 (inside the locked block).
- **Engines:** graphifyy 0.8.39 on the dev Python 3.13.14, and graphifyy 0.8.51 in the uv venv
  `C:/tmp/cl6-842925/prep/v51`. Both carry tree-sitter 0.25.2 and tree-sitter-c-sharp 0.23.5.
- **C# reference:** Roslyn `GetSymbolInfo` over ad-hoc `CSharpCompilation`s (Microsoft.CodeAnalysis.CSharp 4.8.0 from
  the local NuGet cache, .NET SDK 8.0.202).
- **Dispatch truth:** the implementation map (`FindImplementationForInterfaceMember` over the corpus's own types, and
  `OverriddenMethod` chains) is the truth for implementer and override edges.
- The reference is measurement only and never shipped.

## 3. Corpora (pinned)

Fixed in §8.2, with their read-only sources, library prefixes and the commands that re-create and check them.

| Corpus | Pinned SHA | Library prefix (G1) | Truth |
|---|---|---|---|
| jellyfin/jellyfin | dd42a121c43721c8984ba0026d6fbed4a526d01f | every production directory (all but `tests/` and `fuzz/`) | Roslyn |
| dotnet/eShop | 913acea66708a59595351174cee9b29f37179d29 | `src/` | Roslyn |
| the 13 corpora of PREREG.md §3 (this repository at the §1 RC commit) | as pinned there | none (N: identity with V0) | none |

## 4. Spot symbols

Five per C# corpus, plus reserves and the substitution rule, all fixed in §8.3:
- **jellyfin:** `ActivityManager.CreateAsync`, `TmdbClientManager.GetProfileUrl`, `RequestHelpers.GetUserId`,
  `ItemResolveArgs.GetCollectionType`, `MediaSourceInfo.InferTotalBitrate`.
- **eShop:** `OrderRepository.GetAsync`, `OrderStatusNotificationService.NotifyOrderStatusChangedAsync`,
  `UriHelper.CombineUri`, `CustomerBasket.AddItemToBasket`, `Order.AddOrderItem`.

## 5. Variants

V0, VT-cs (`DEEPMAP_TYPED_CALLS=csharp`), VT-cs[shape] (one arm per shape), the reported ablations VT-cs+t,
VT-cs+np, VT-cs-ifs and VT-cs-if0, and VRC-cs, the exact flip combination. All are fixed in §8.4, with PREREG §5's
inert-variant VOID rule and its C# additions.

## 6. Tools per layer

- **G:** `offline_calls.py`, `truth_roslyn.py` + `truth_roslyn/` (new), `compare_calls.py`, `delta.py`, `impact_spot.py`,
  `indegree_drift.py`, and `run_matrix.sh` (its C# tables).
- **N:** `offline_calls.py` and a byte comparison.
- **E:** `engdiff.py` and `tests/typed_calls_engine_parity_gates.py --corpora`.
- **A:** `audit_sample.py`.
- **S:** `tests/stage0_refreeze.py --env`.
- **R (VRC-cs only):** `run_variant.py`, `measure_self.py` + `gt_self65.json`.
- **P:** the sync's own records (`.deepmap/graph_build.json`; the sync log).

The kit changes this needs, all before the first RC run, are listed in §8.6.

## 7. PILOT: none. What exists at registration (no DeepMap data)

The spot symbols and the corpus facts below come from reading code only: a tree-sitter-c-sharp 0.23.5 parse
(tree-sitter 0.25.2, Python 3.13.14), ripgrep 14.1.1 and scc 3.7.0, run outside this repository with no DeepMap
module imported. They are kept in `C:/tmp/deepmap-cs-d3/_measurement_aids/`:

| File | sha256 |
|---|---|
| spots.json (the 10 spots and 8 reserves, every call site with its form, test flag and overload flag) | 540be4ff9f55ba220cb926b8cdae41f1b5bb683a4d1f31b23e825ec829e0c65f |
| census/jellyfin_census.json | 70f5e492fe09e6d239bdbc96de994772142e8aef506a87dd43d7d21938ee6273 |
| census/eshop_census.json | 7ce4aaa01c3eecf65decac359d6c28e3b7e2886a4075bbb844755a888f247a73 |

Corpus facts. They are context only: no threshold in this file is derived from them, and the census's form names
are its own approximations, not the reference's.

| | jellyfin | dotnet/eShop |
|---|---|---|
| `.cs` files | 2,091 (1,893 production; 196 under tests/, 2 under fuzz/) | 547 (498 under src/, 49 under tests/) |
| `.csproj` | 42 (`Jellyfin.sln`) | 28 (`eShop.slnx`) |
| Lines (scc: all / code) | 317,661 / 219,730 | 26,354 / 20,691 |
| tree-sitter-c-sharp clean parse | 2,090 of 2,091 (`MediaBrowser.MediaEncoding/Subtitles/SubtitleEditParser.cs:121`) | 545 of 547 (`src/ClientApp/Services/Basket/Protos/Basket.cs`, `src/ClientApp/MauiProgram.cs`: `#if`) |
| Target; usings | net10.0, `LangVersion latest`; no implicit or global usings | net10.0; `ImplicitUsings` enabled in Directory.Build.props; 24 `GlobalUsings.cs` |
| All invocations | 83,420 | 6,345 |
| Cross-file calls to project methods, production, lower to upper bound | name_attr 4,072 to 11,069; recv 77 to 2,264; bare 207 to 1,516; base 135 to 143; this 0 to 17; extension methods 958 | name_attr 181 to 523; recv 44 to 150; bare 17 to 88; base 2 to 10; this 1 to 7; extension methods 41 |
| Interface-typed call sites by implementer count (1 / 2 to 4 / more than 4 / 0) | 2,389 / 54 / 39 / 11 | 109 / 58 / 0 / 2 |

Facts that shaped the protocol:
- **V0 sees none of the spots' callers, by construction.** On both engine ends graphify marks every dotted C# call
  a member call and drops every cross-file member call; DeepMap adds nothing with the call-linking flags off. No spot
  has a caller in its own declaring file, so every spot's expected V0 hop-1 recall is 0 (§8.3 checks it at RC).
- **Roslyn 4.8.0 knows C# 12.** Both corpora target net10.0; a ripgrep sweep found no C# 13 or 14 syntax (no
  `extension` blocks, `field` keyword, `?.x =` assignment or partial properties).
- **eShop needs one compilation per project:** project references, SDK implicit usings, `GlobalUsings.cs` files,
  `..\Shared\*.cs` links, top-level statements, and global-namespace `Extensions` classes in 4 projects.
- **Out of scope, never counted as a miss of this decision:** MediatR `Send` to `Handle` dispatch (eShop's 12
  `mediator.Send(` sites reach handlers that have no direct callers), property reads, and `.razor` files.
- **The reference tool ran on jellyfin before registration**, while it was developed: a synthetic self-test, then a
  timing and coverage check (2,039 files compiled; 41,124 call sites, of which 11,661 bound to project code; about
  24 s on 12 threads). That output was deleted, and §8.1's last bullet applies to it. It holds no DeepMap number, and
  no threshold here was derived from it.
- **This repository holds one `.cs` file at the RC:** `benchmark/call_linking/truth_roslyn/Program.cs`, the reference
  tool's own source (`git ls-files` held none on 2026-10-28, before the tool was added). So the `self` N corpus is
  judged by N2's rule, and R2 and R3 are measured live (§8.7 N1, R). Both STAGE-0 oracles read the pinned
  9c8d50bce corpus, which holds no `.cs` file.

## Amendments before RC

All dated 2026-10-28, before any DeepMap data for either C# corpus. Their source is the kit build and its independent
check: the reference tool brought to §8.2, the scoring kit, the gates and a checker that probed each clause. None
changes a criterion or a threshold. Each governs the protocol items it names. The harness gate locks this section's
sha256 beside the block's before step 1.

- **AC1. Interface dispatch reaches the override that runs** (§8.2 `dispatch`, first bullet). When
  `T.FindImplementationForInterfaceMember` returns a virtual, abstract or override class member, the target is the
  most-derived override of it with a body on T's base chain: the method an object of exact type T runs.
  - Reason: for the common shape interface → abstract base → override, the registered text yields the body-less base
    slot and so no target, while §8.6 A's sheet sentence makes the override TRUE.
  - Measured on the reference's development runs: 31 jellyfin sites (97 targets); none on eShop. Left as registered,
    a correct product edge would be scored refuted and counted as a reference error.
- **AC2. Override-rule receivers** (§8.2 `dispatch`, second bullet).
  - An interface-typed receiver calling an `object` member (ToString, Equals, GetHashCode) has no override targets.
  - A type-parameter receiver is read as its class constraint, else `System.Object` (`System.ValueType` under a
    `struct` constraint).
- **AC3. Compilations, as MSBuild builds them** (§8.2 Compilations).
  - **Project references:** the transitive closure (unless `DisableTransitiveProjectReferences` is true), in
    dependency order, ties broken by byte order of the paths. Under `Aspire.AppHost.Sdk`, a `<ProjectReference>`
    without `IsAspireProjectResource="false"` is a resource, not an assembly reference.
  - **Implicit usings:** they apply when `ImplicitUsings` is `enable` or `true` in the project or in the
    Directory.Build.props files MSBuild imports for it (the nearest one above it and those it imports), the last
    assignment winning. An SDK not listed (`Aspire.AppHost.Sdk`, `MSTest.Sdk`) takes Microsoft.NET.Sdk's list;
    SDK 8.0.202's `.Razor` adds none of its own.
  - **Generated assembly attributes:** MSBuild's `InternalsVisibleTo` attributes (from `<InternalsVisibleTo Include>`
    and `<AssemblyAttribute Include="System.Runtime.CompilerServices.InternalsVisibleTo">` items) are added, unless
    `GenerateAssemblyInfo` is false. Reason: without them, test calls into internal members come out inaccessible
    and therefore unresolved (41 jellyfin sites, including J3's 4 test callers; 2 on eShop).
  - **MAUI single-project layout** (`UseMaui` true): a file under `Platforms/<platform>/` is compiled only when
    `<platform>` is the first target framework's platform, as the MAUI SDK's own `Compile Remove` does. Reason:
    otherwise every platform's copy of a type lands in one compilation (eShop ClientApp and HybridApp).
  - **The leftover compilation** holds only git-tracked `.cs` files that no project compiles and that are in no
    project's tree. It is built with `LanguageVersion.CSharp12`, no preprocessor symbols and no implicit usings, and
    references every project.
- **AC4. Partial methods** (§8.2 `defs`). For a partial method's implementation part, the alt lines also hold the
  defining declaration's first line: graphify's only node is there. No partial member of either corpus has a source
  implementation today.
- **AC5. Row fields the scoring reads** (§8.2 Format; §8.5; G4c).
  - Every row carries `dispatch` (a list, empty when there is none). A row with dispatch targets carries
    `dispatch_rule` (`interface` or `override`). Every def carries `interface` (true when it is an interface member).
  - G4c and §8.5's fallback stratum read `dispatch_rule` for a row with dispatch targets, else the defs' `interface`.
    So a static target declared outside the corpus (IDisposable.Dispose) still says which rule applied.
- **AC6. "Resolved" in the refuted test** (§8.5). A resolved site is one bound, statically or by dispatch, to a
  definition that maps to a graph node, exactly as for jedi and tsc. A site bound only to a symbol outside the
  corpus, or to a definition with no node, is not resolved, so an edge from its caller is unverified, not refuted.
- **AC7. The RC snapshot** (§1, §3, §8.2, §8.6 P, G5a, N1).
  - §1's snapshot is ONE full-tree snapshot of this repository at the RC suite run: a clone with the working tree's
    tracked and untracked-not-ignored files copied over it, the working tree's deleted tracked files removed, and
    `git add -A` run in the clone (`run_matrix.sh`, `CL_PHASE=self-snapshot`).
  - Every RC command runs from it: offline_calls, truth_roslyn, the kit, `benchmark/clone_graph.py`,
    `build/deepmap_cli.py sync` and the G5a gates. "The RC commit" in §3, §8.2, G5a and N1 means this snapshot, and
    `self` is this snapshot.
  - **Kit hash:** `offline_calls.py kithash`.
  - **Reference tool identity:** the §1 scheme over `truth_roslyn.py` and `truth_roslyn/{Program.cs,
    TruthRoslyn.csproj, global.json, nuget.config}` (`truth_roslyn.py identity`), plus the versions its `resolver`
    block prints.
- **AC8. jellyfin's library prefix** (§8.2) is the explicit directory list. The root file `SharedVersion.cs` holds
  two assembly attributes and no call site.
- **AC9. The dispatch stratum** (§8.4, §8.5, G3b). The product's `DEEPMAP_TYPED_CALLS_IFACE` governs interface AND
  virtual dispatch.
  - VT-cs-ifs keeps only the statically bound member, interface or virtual. VT-cs-if0 has neither dispatch rule.
  - G3b's stratum is the `added` bucket of delta(VT-cs-ifs, VT-cs): the implementer and override edges. Its
    thresholds are unchanged.
  - The fallback reads "whose static target is an interface, virtual, abstract or override member other than b",
    through AC5's fields.
- **AC10. Exhaustive removals** (§8.6 A). "Every removed edge the reference confirms" includes the
  dispatch-confirmed ones.
- **AC11. The reference error rate** (§8.6 A). It is computed per corpus × engine, over the union of that engine's
  main and implementer samples, with a pair audited in both counted once. A corpus is unreliable when either
  engine's rate is 10% or more.
- **AC12. The spot command** (§8.6 G). `impact_spot.py ... --corpus=<id>` measures the corpus's FINAL spot list from
  step 3: `--final=<id>/spots_final.json`, the output of `impact_spot.py substitute`.
- **AC13. Engine parity** (§8.6 E, G6b).
  - The gate command is `python tests/typed_calls_engine_parity_gates.py --corpora --only
    jellyfin__jellyfin,dotnet__eShop --py39 <dev python> --py51 C:/tmp/cl6-842925/prep/v51/Scripts/python.exe`.
  - G6b compares the typed tuples on the edges whose both endpoints exist on both engines (PREREG amendment A1). The
    raw comparison is reported beside it, never decisive.
- **AC14. The N3 gate** (§8.7 N3, G5a). It is `tests/typed_calls_push_gates.py`'s csharp identity section (the push
  output of its corpus under `DEEPMAP_TYPED_CALLS=csharp` equals the flags-off run, on both engines), together with
  `tests/typed_calls_cs_gates.py` section G (non-`.cs` callers' tuples unchanged). Both are G5a gates.
- **AC15. The G5b manifest** (§8.7 G5b).
  - Each line is `<sha256>  <path relative to benchmark/call_linking/cs/>`, with forward slashes, in byte order of
    the paths' UTF-8 bytes. The manifest itself and any `sheet.md` are never listed.
  - `cs/.gitattributes` holding `* -text` is placed with the files and listed, so a Windows checkout keeps the bytes.
  - DECISION_CS.md records the manifest's sha256 on a line naming `cs/MANIFEST.sha256`.
- **AC16. The VRC-cs flags** (§8.4, §8.10). The flags are `--set DEEPMAP_TYPED_CALLS=<every language in the RC tree's
  _TYPED_DEFAULT_LANGS>,csharp`, because an explicit value replaces the default set. Today that is `csharp`.
- **AC17. Editorial** (§8.3). "(§2)" refers to §7.
- **AC18. Disclosure.** The reference tool's development runs on both corpora (outputs deleted) counted hop-1
  reference callers per spot. J2 (`TmdbClientManager.GetProfileUrl`) has none: all 11 of its sites pass TMDbLib
  types, which the contract does not reference. Step 3's substitution rule will therefore replace it mechanically.
  The spots and the rule are unchanged, and no DeepMap code has run on either corpus.

<!-- PREREG-CS-PROTOCOL BEGIN (sha256 locked by tests/call_linking_harness_gates.py; never edited after the first RC run) -->
## 8. Measurement protocol (D3-cs)

### 8.1 Pre-registration and the order of the RC run

- Its protocol block is hash-locked by the harness gate and §1 is filled before step 1, so the reference truth is
  made by the frozen tool. The operator commits this file; the RC run does not wait for that commit (§1).
- The RC run goes in this order. A step starts only after the previous one is recorded in RESULTS_CS.md:
  1. **Reference truth.** truth_roslyn (§8.2) on both C# corpora, run twice. The two outputs must be
     byte-identical; their sha256 is recorded.
  2. **V0.** V0 dumps of both C# corpora on both engines, each run twice and byte-identical. The first of these dumps
     is **the first RC run**.
  3. **Spots.** The spot check of §8.3 on the V0 dumps, and its substitutions. The final spot list and its V0 numbers
     are recorded before any other variant's dump of a C# corpus exists.
  4. **Everything else:** every other arm of §8.4 on the C# corpora; V0 and VT-cs on the N corpora; E; the deltas;
     the audit samples (drawn, reviewed, scored); S; P.
  5. **The record.** RESULTS_CS.md. Then the maintainer commits the G5 artifacts (§8.7 G5b). Then DECISION_CS.md,
     derived twice, independently, from the raw artifacts: where the derivations differ, the strict reading wins,
     and a criterion that was not computed exactly does not hold. Only if D3-cs passes: VRC-cs (§8.10).
- A DeepMap dump of either C# corpus made before step 2 is not data; RESULTS_CS.md reports any that exists.
- truth_roslyn may be developed and checked on fixtures and on the corpora before §1 is filled (reference output is
  not DeepMap data). Every such output is discarded, and step 1 regenerates the truth with the frozen tool.

### 8.2 Environment, engines, reference resolver, corpora

| Role | Build |
|---|---|
| Engine, dev end | graphifyy 0.8.39 on the dev Python 3.13.14 (tree-sitter 0.25.2, tree-sitter-c-sharp 0.23.5, tree-sitter-python 0.25.0, tree-sitter-typescript 0.23.2, tree-sitter-javascript 0.25.0, networkx 3.6.1) |
| Engine, CI end | graphifyy 0.8.51 in the uv venv `C:/tmp/cl6-842925/prep/v51` (`Scripts/python.exe`, Python 3.13.14; tree-sitter 0.25.2, tree-sitter-c-sharp 0.23.5, tree-sitter-python 0.25.0, tree-sitter-typescript 0.23.2, tree-sitter-javascript 0.25.0, networkx 3.7) |
| C# reference (static binding) | Roslyn: Microsoft.CodeAnalysis.CSharp 4.8.0 with Microsoft.CodeAnalysis.Common 4.8.0, restored offline from the local NuGet cache (`%USERPROFILE%/.nuget/packages` as the only package source), built with .NET SDK 8.0.202 as a net8.0 console tool, `benchmark/call_linking/truth_roslyn/`. `SemanticModel.GetSymbolInfo` over ad-hoc `CSharpCompilation`s; no MSBuild workspace |
| C# reference (dispatch) | the same tool: `ITypeSymbol.FindImplementationForInterfaceMember` over every type declared in the corpus's source, and `IMethodSymbol.OverriddenMethod` chains |
| Environment | `PYTHONUTF8=1`, `PYTHONHASHSEED=0`, `DEEPMAP_GRAPHIFY_TIMEOUT=3600`; `CL_WORK` and `DEEPMAP_HOME` are short scratch directories outside this repository (Windows MAX_PATH) |

- The two engine builds are the ends of the pinned range `graphifyy>=0.8.39,<0.8.52`; any other build is not RC data.
  Every dump runs `offline_calls.py --expect-engine graphify`: an extraction that did not resolve to graphify is VOID
  (exit 3). The ast engine reads no C#.
- **The reference is measurement only.** It is never in requirements.txt, sbom.cdx.json, NOTICE,
  THIRD-PARTY-LICENSES.md, an installer or a plugin manifest. Nothing under build/ or hooks/ references truth_roslyn.py
  or truth_roslyn/ (harness gate sections A and G cover both).

**The reference contract.** This is what the reference emits, through `benchmark/call_linking/truth_roslyn.py` (the
stdlib driver) and the .NET tool in `truth_roslyn/`. It is fixed now, and §1 freezes the code. The options the
registered run passes to emit exactly this contract are recorded in RESULTS_CS.md with the reference tool identity.
- **Format.** The kit's `call-linking/truth/1` rows, carrying each site's dispatch targets: either a per-row
  `dispatch` list, or per-def implementation and override lists from which the kit derives exactly the set defined
  below (the derivation is gate-locked). A `resolver` block records the Microsoft.CodeAnalysis version, SDK, runtime,
  language version, references and each project's preprocessor symbols.
- **Compilations.** One per git-tracked `.csproj`, so same-named types of different projects, per-project implicit
  usings and top-level statements stay apart (eShop has all three).
  - **Files:** every git-tracked `.cs` under the project's directory that no deeper `.csproj` claims, minus `bin/`
    and `obj/` and `<Compile Remove>` items, plus `<Compile Include>` links.
  - **References:** the reference assemblies of `Microsoft.NETCore.App.Ref` 8.0.3 and `Microsoft.AspNetCore.App.Ref`
    8.0.3 (`C:/Program Files/dotnet/packs/`), plus the compilations of the project's `<ProjectReference>`s, in
    dependency order. A reference with `OutputItemType="Analyzer"` or `ReferenceOutputAssembly="false"` is left out.
  - **No NuGet package assembly** is referenced. A call that needs a package type to bind comes out unresolved or
    external.
  - **Implicit usings:** the SDK's list for the project's `Sdk` (Microsoft.NET.Sdk; .Web, .Worker, .Razor and
    .BlazorWebAssembly add their own). They apply when `ImplicitUsings` is `enable` or `true` in the project or in a
    `Directory.Build.props` above it. `<Using Include>` items are added and `<Using Remove>` items removed.
  - **Parse options:** `LanguageVersion.CSharp12` (the newest 4.8.0 knows). The preprocessor symbols are those
    MSBuild defines for a Release build of the project's first target framework (TRACE, RELEASE, the
    target-framework symbols, the platform symbol of a platform-suffixed framework), plus its `<DefineConstants>`.
  - No analyzer or source generator runs.
  - A file in no project's tree is compiled once, in a leftover compilation that references every project.
  - A linked file's rows come from one compilation that holds it: its own directory's project first, then the
    others in byte order of their paths.
- **Sites.** One row per `InvocationExpressionSyntax` (`nameof(...)` excluded) and per object creation
  (`new T(...)`, `new T { }`, target-typed `new(...)`), in every compiled `.cs` file. A file with Roslyn syntax errors
  stays in (Roslyn binds what it can) and is named in `parse_errors` with its error count. These are not call sites:
  constructor initializers (`: base(...)`, `: this(...)`), attributes, query clauses, collection initializers,
  `foreach`, `using`, `await`, operators and conversions.
- **`form`.** One per site, read from the syntax, with the kit's FORM_ORDER names:
  - `bare`: `M(...)` or `M<T>(...)`.
  - `this`: `this.M(...)`.
  - `super`: `base.M(...)`.
  - `name_attr`: `x.M(...)` where x is a simple name, with or without type arguments, or a predefined type keyword.
    That covers a field, property, parameter, local, type name or namespace alias.
  - `recv`: any other receiver, for example `a.b.M()`, `this._f.M()`, `f().M()`, `x[i].M()`, a cast,
    `new T().M()`, `global::...` or a qualified type `Ns.T.M()`. `x?.M(...)` takes the form `x.M(...)` would; a
    longer conditional chain is `recv`.
  - `ctor`: an object creation.
  - `other`: `(f)(...)`, `p->M()` and anything else.
- **`name`.** The invoked member's identifier without type arguments; for an object creation, the type's simple name.
- **`defstack`.** The enclosing members, outermost first: method, constructor, destructor, operator, conversion
  operator, property, indexer and event declarations. Each entry is [qualified name (containing types, then the
  member; a constructor is named after its type), the member's first line counting its attribute lists, alt lines].
  - Lambdas and anonymous methods are not entries: graphify's C# walk gives their calls to the enclosing member. A
    local function may be an entry; it has no graph node, so the enclosing member stays the caller.
  - The alt lines are the member's first line after its attribute lists and its name line. For an overloaded method
    or constructor, they also hold the first line of the first same-named member of the same type declaration
    (graphify makes one node for all overloads).
- **`defs` (the STATIC binding).** Only `GetSymbolInfo(site).Symbol` counts; a site with only candidate symbols is
  unresolved (their count is reported).
  - The symbol is taken as its original definition: the `ReducedFrom` method for an extension-method call,
    `OriginalDefinition` for a generic method or a member of a constructed type, and `PartialImplementationPart` when
    a partial method has one.
  - An object creation binds to the created TYPE (type `class`; every declaration of a partial type).
  - Every declaration in a compiled corpus file is a def. Its line is its first line counting attribute lists, with
    the alt lines as in `defstack`. A symbol declared outside the corpus gives no def (external).
- **`dispatch` (the runtime targets beyond the static binding).** This is the truth for implementer and override
  edges.
  - **S is a non-static member of an interface.** The targets are every method with a body, declared in corpus
    source, that `T.FindImplementationForInterfaceMember` returns for S. T ranges over every non-interface type
    declared in corpus source that implements S's interface and either is the receiver's static type or derives from
    it or implements it; for a type-parameter receiver, every type implementing S's interface. The member asked for
    is the one in T's own constructed view of S's interface.
  - **S is a virtual, abstract or override class member,** called through any receiver but `base.`. The targets are
    every override with a body, declared in corpus source, whose `OverriddenMethod` chain reaches S's root
    declaration, on a type that is the receiver's static type or derives from it (for `this.` or an implicit
    receiver, the containing type).
  - **Otherwise there are none:** a static, non-virtual or sealed member, a `base.` call, an extension method or an
    object creation.
  - S itself is never a dispatch target, nor is a bodiless declaration (abstract, extern, a partial definition
    without implementation).
  - For a caller that is not in a test file (the product's own rule, `sync._is_test_path`), a target declared in a
    test file is excluded.
- **Determinism.** Rows are sorted by (file, line, col, name), the output has no timing, and two runs are
  byte-identical.

**The C# corpora (pinned):**

| Corpus id | Upstream | Pinned SHA | Read-only source | Library prefix (G1) | Licence |
|---|---|---|---|---|---|
| jellyfin__jellyfin | https://github.com/jellyfin/jellyfin (`master`) | dd42a121c43721c8984ba0026d6fbed4a526d01f | `C:/tmp/deepmap-cs-d3/jellyfin` | every production directory: `Emby.Naming/`, `Emby.Photos/`, `Emby.Server.Implementations/`, `Jellyfin.Api/`, `Jellyfin.Data/`, `Jellyfin.Server/`, `Jellyfin.Server.Implementations/`, `MediaBrowser.Common/`, `MediaBrowser.Controller/`, `MediaBrowser.LocalMetadata/`, `MediaBrowser.MediaEncoding/`, `MediaBrowser.Model/`, `MediaBrowser.Providers/`, `MediaBrowser.XbmcMetadata/`, `src/` (everything but `tests/` and `fuzz/`) | GPL-2.0 (measurement only; nothing from it ships) |
| dotnet__eShop | https://github.com/dotnet/eShop (`main`) | 913acea66708a59595351174cee9b29f37179d29 | `C:/tmp/deepmap-cs-d3/eshop` | `src/` | MIT |

Each run copies a corpus from its read-only source and checks it before use:
```sh
git clone --no-hardlinks -c core.autocrlf=false -c core.eol=lf <read-only source> <CL_WORK>/<corpus id>
git -C <CL_WORK>/<corpus id> checkout --detach <pinned SHA>
git -C <CL_WORK>/<corpus id> rev-parse HEAD                     # must print the pinned SHA
git -C <CL_WORK>/<corpus id> status --short                     # must print nothing
git -C <CL_WORK>/<corpus id> ls-files --eol -- '*.cs' | awk '{print $2}' | sort | uniq -c   # only w/lf
```
A copy in any other state is VOID. `C:/tmp/deepmap-cs-d3` is a read-only root (`CALL_LINKING_READONLY_ROOTS`) in
every RC run.

To re-create a read-only source from upstream:
```sh
git init <dir>/jellyfin
git -C <dir>/jellyfin remote add origin https://github.com/jellyfin/jellyfin.git
git -C <dir>/jellyfin fetch --depth 1 origin dd42a121c43721c8984ba0026d6fbed4a526d01f
git -C <dir>/jellyfin checkout --detach FETCH_HEAD
git clone -c core.autocrlf=false -c core.eol=lf https://github.com/dotnet/eShop.git <dir>/eshop
git -C <dir>/eshop checkout --detach 913acea66708a59595351174cee9b29f37179d29
```
(jellyfin's `.gitattributes` holds `* text=auto eol=lf`. eShop's holds `* text=auto`, so without the `-c` flags a
machine with `core.autocrlf=true` checks it out as CRLF.)

**The N corpora (non-C# identity).** Every corpus of PREREG.md §3 at its pinned SHA, materialized as PREREG.md §3
and run_matrix.sh do:
- the five Python benchmark corpora (requests, flask, click, werkzeug, fastapi);
- the STAGE-0 build/ corpus (9c8d50bceab96288f83877c5306167b54b6d918a);
- zod, nestjs-realworld and node-express-boilerplate;
- gin, httprouter and serde-json;
- this repository at the RC commit of §1 as `self` (from that commit, not the working tree).

That is 13 corpora.

**Retrieval ground truth.** benchmark/questions.yaml holds 49 questions on 9 repositories: requests 6, flask 8,
click 6, werkzeug 6, fastapi 7, zod 4, gin 4, httprouter 4, serde-json 4. benchmark/repos.yaml lists no C#
repository. No C# question exists.

### 8.3 Spot symbols (hop-1 impact, G1c)

**How they were chosen.** By code reading only, before any DeepMap output existed for either corpus (§2):
- Each spot is a project method called from other files through a receiver shape typed linking targets.
- No spot has a caller in its own declaring file.
- No spot call goes through an interface with more than one implementer.

**How a spot is measured.**
- A spot is `Name@file`: the graph nodes whose name is Name or ends with `.Name`, in that file (`impact_spot.py`).
- Its reference hop-1 callers are every caller holding a static or dispatch pair to that node (T_s ∪ T_d, §8.5).
- It "improves" when its hop-1 recall under VT-cs is strictly above V0's on that engine.

The grep counts below are enclosing members (methods, constructors) found by code reading. The decision uses the
Roslyn reference, not these counts. The expected V0 hits are 0 by construction (§2) and were not measured.

| # | Symbol | Corpus | Definition | Receiver shape (typed-calls shape) | Grep callers | Expected V0 hits, 0.8.39 | Expected V0 hits, 0.8.51 | Callers in detail |
|---|---|---|---|---|---|---|---|---|
| J1 | `ActivityManager.CreateAsync` | jellyfin | `Jellyfin.Server.Implementations/Activity/ActivityManager.cs:39`; interface member `MediaBrowser.Model/Activity/IActivityManager.cs:25` | interface-typed DI field `_activityManager` (R3 + interface dispatch; one implementer) | 18 | 0 | 0 | 18 production, 18 files |
| J2 | `TmdbClientManager.GetProfileUrl` | jellyfin | `MediaBrowser.Providers/Plugins/Tmdb/TmdbClientManager.cs:589` | concrete-class field `_tmdbClientManager` (R3) | 5 | 0 | 0 | 5 production, 5 files, 11 sites |
| J3 | `RequestHelpers.GetUserId` | jellyfin | `Jellyfin.Api/Helpers/RequestHelpers.cs:67` (`internal static`, in a static class) | static type name `RequestHelpers.GetUserId(User, userId)` (K) | 82 | 0 | 0 | 78 production + 4 test, 29 files, 83 sites |
| J4 | `ItemResolveArgs.GetCollectionType` | jellyfin | `MediaBrowser.Controller/Library/ItemResolveArgs.cs:224` | class-typed parameter `args` (R1) | 8 | 0 | 0 | 8 production, 8 files |
| J5 | `MediaSourceInfo.InferTotalBitrate` | jellyfin | `MediaBrowser.Model/Dto/MediaSourceInfo.cs:148` | local `var x = new MediaSourceInfo {...}` (R2, 4 sites), class-typed parameter (R1, 4), `foreach var` (1) | 9 | 0 | 0 | 9 production, 7 files; 2 overloaded callers |
| E1 | `OrderRepository.GetAsync` | eShop | `src/Ordering.Infrastructure/Repositories/OrderRepository.cs:21`; interface member `src/Ordering.Domain/AggregatesModel/OrderAggregate/IOrderRepository.cs:12` | interface-typed field `_orderRepository` in MediatR handlers (R3 + interface dispatch; one implementer, in another project) | 13 | 0 | 0 | 12 production + 1 test, 13 files |
| E2 | `OrderStatusNotificationService.NotifyOrderStatusChangedAsync` | eShop | `src/WebApp/Services/OrderStatus/OrderStatusNotificationService.cs:27` | concrete class captured by a primary-constructor parameter (R3) | 6 | 0 | 0 | 6 production, 6 files |
| E3 | `UriHelper.CombineUri` | eShop | `src/ClientApp/Helpers/UriHelper.cs:6` (`public static class`) | static helper `UriHelper.CombineUri(...)` (K); 4 of the 9 sites inside interpolated strings | 9 | 0 | 0 | 9 production, 2 files |
| E4 | `CustomerBasket.AddItemToBasket` | eShop | `src/ClientApp/Models/Basket/CustomerBasket.cs:11` | parameter (R1), `CustomerBasket basket = new()` (R2), `var basket = await ...GetBasketAsync()` (unknown by design), a field in a constructor (R3) | 5 | 0 | 0 | 5 production, 4 files, 6 sites |
| E5 | `Order.AddOrderItem` | eShop | `src/Ordering.Domain/AggregatesModel/OrderAggregate/Order.cs:71` | local `var order = new Order(...)` (R2), a local from the static factory `Order.NewDraft()` (R4), a test field (R3) | 3 | 0 | 0 | 2 production + 1 test, 3 files |

**Call sites found by grep** (file:line, with the caller). They are recorded in full, with the receiver and the
form, in spots.json (§2).
- **J1:** all through `_activityManager`.
  - `Jellyfin.Api/Controllers/LibraryController.cs:1001` (`LogDownloadAsync`).
  - The `OnEvent` methods under `Jellyfin.Server.Implementations/Events/Consumers/`: `Library/LyricDownloadFailureLogger.cs:37`, `Library/SubtitleDownloadFailureLogger.cs:37`,
    `Security/AuthenticationFailedLogger.cs:35`, `Security/AuthenticationSucceededLogger.cs:33`, `Session/PlaybackStartLogger.cs:61`, `Session/PlaybackStopLogger.cs:69`,
    `Session/SessionEndedLogger.cs:38`, `Session/SessionStartedLogger.cs:38`, `System/TaskCompletedLogger.cs:67`, `Updates/PluginInstallationFailedLogger.cs:35`,
    `Updates/PluginInstalledLogger.cs:35`, `Updates/PluginUninstalledLogger.cs:35`, `Updates/PluginUpdatedLogger.cs:35`, `Users/UserCreatedLogger.cs:33`,
    `Users/UserDeletedLogger.cs:34`, `Users/UserLockedOutLogger.cs:35`, `Users/UserPasswordChangedLogger.cs:33`.
  - Must-not: `Jellyfin.Server/Migrations/JellyfinMigrationService.cs:113` `databaseCreator.CreateAsync` (an EF method of the same name).
- **J2:** all through `_tmdbClientManager`, under `MediaBrowser.Providers/Plugins/Tmdb/`:
  `Movies/TmdbMovieProvider.cs:299,346` (`GetMetadata`), `People/TmdbPersonProvider.cs:55,79` (`GetSearchResults`),
  `TV/TmdbEpisodeProvider.cs:227,258,299` (`GetMetadata`), `TV/TmdbSeasonProvider.cs:103,144` (`GetMetadata`),
  `TV/TmdbSeriesProvider.cs:367,409` (`GetPersons`).
- **J3:** static `RequestHelpers.GetUserId(...)` in action methods under `Jellyfin.Api/Controllers/`:
  - ArtistsController.cs:125,297,407; ChannelsController.cs:66,131,174; DevicesController.cs:55;
    DisplayPreferencesController.cs:56,121; FilterController.cs:61,126; GenresController.cs:96,161; ImageController.cs:106,206;
  - InstantMixController.cs:82,125,168,210,247,290,374; ItemsController.cs:265,940,1074,1131; LibraryController.cs:158,232,460,493,746,816;
  - LiveTvController.cs:164,224,289,387,412,574,716,757; MediaInfoController.cs:77,152,167,287; MoviesController.cs:68;
    MusicGenresController.cs:97,152; PersonsController.cs:93,140; PlaylistsController.cs:91,379; PlaystateController.cs:81,147;
  - QuickConnectController.cs:110; SearchController.cs:103; SessionController.cs:60; StudiosController.cs:91,144;
    SuggestionsController.cs:77; TvShowsController.cs:93,151,222,340; UniversalAudioController.cs:119;
  - UserLibraryController.cs:82,130,169,221,269,317,367,416,466,536; UserViewsController.cs:73,133; VideosController.cs:99;
    YearsController.cs:91,178.
  - Tests: `tests/Jellyfin.Api.Tests/Helpers/RequestHelpersTests.cs:39,57,75,96`.
  - Must-not: the extension twin `Jellyfin.Api/Extensions/ClaimsPrincipalExtensions.cs:18` `GetUserId(this ClaimsPrincipal)`,
    about 67 `User.GetUserId()` sites, three of them inside `RequestHelpers.cs` (lines 69, 96, 125).
- **J4:** `args` inside `Resolve` methods under `Emby.Server.Implementations/Library/Resolvers/`:
  - `Audio/AudioResolver.cs:81`, `Audio/MusicAlbumResolver.cs:56`, `Audio/MusicArtistResolver.cs:67`, `Books/BookResolver.cs:23`;
  - `Movies/MovieResolver.cs:93`, `PhotoAlbumResolver.cs:47`, `TV/EpisodeResolver.cs:55`, `TV/SeriesResolver.cs:62`.
  - Must-nots: `LibraryManager.GetCollectionType(string)` (`Emby.Server.Implementations/Library/LibraryManager.cs:1590`, called bare at :1564)
    and `SpecialFolderResolver.GetCollectionType(ItemResolveArgs)` (:66, called bare at :57).
- **J5:**
  - Parameters: `Emby.Server.Implementations/Library/LiveStreamHelper.cs:183` (`AddMediaInfoWithProbe`, overloaded),
    `Emby.Server.Implementations/Library/MediaSourceManager.cs:690` (`AddMediaInfo`), `:873` (`AddMediaInfoWithProbe`),
    `src/Jellyfin.LiveTv/LiveTvMediaSourceProvider.cs:239` (`Normalize`).
  - `foreach var`: `MediaSourceManager.cs:316` (`GetDynamicMediaSources`, overloaded).
  - Local `new`: `MediaBrowser.Controller/Entities/BaseItem.cs:1218` (`GetVersionInfo`), `MediaBrowser.MediaEncoding/Probing/ProbeResultNormalizer.cs:265` (`GetMediaInfo`),
    `src/Jellyfin.LiveTv/TunerHosts/HdHomerun/HdHomerunHost.cs:340` (`GetMediaSource`), `src/Jellyfin.LiveTv/TunerHosts/M3UTunerHost.cs:208` (`CreateMediaSourceInfo`).
- **E1:** `Handle` methods through `_orderRepository`, under `src/Ordering.API/Application/`:
  - `Commands/CancelOrderCommandHandler.cs:21`, `Commands/SetAwaitingValidationOrderStatusCommandHandler.cs:21`, `Commands/SetPaidOrderStatusCommandHandler.cs:24`,
    `Commands/SetStockConfirmedOrderStatusCommandHandler.cs:24`, `Commands/SetStockRejectedOrderStatusCommandHandler.cs:24`, `Commands/ShipOrderCommandHandler.cs:21`;
  - `DomainEventHandlers/OrderCancelledDomainEventHandler.cs:27`, `DomainEventHandlers/OrderShippedDomainEventHandler.cs:27`,
    `DomainEventHandlers/OrderStatusChangedToAwaitingValidationDomainEventHandler.cs:27`, `DomainEventHandlers/OrderStatusChangedToPaidDomainEventHandler.cs:26`,
    `DomainEventHandlers/OrderStatusChangedToStockConfirmedDomainEventHandler.cs:27`, `DomainEventHandlers/UpdateOrderWhenBuyerAndPaymentMethodVerifiedDomainEventHandler.cs:21`.
  - Test: `tests/Ordering.UnitTests/Application/NewOrderCommandHandlerTest.cs:31` (an `IOrderRepository` field holding an NSubstitute mock).
  - Must-nots: `RequestProvider.GetAsync` / `IRequestProvider.GetAsync` (ClientApp, 7 sites), `HttpClient.GetAsync` (about 30 test sites), `SecureStorage.GetAsync`.
- **E2:** the `Handle` methods of
  `src/WebApp/Services/OrderStatus/IntegrationEvents/EventHandling/OrderStatusChangedTo{AwaitingValidation,Cancelled,Paid,Shipped,StockConfirmed,Submitted}IntegrationEventHandler.cs:13`,
  receiver `orderStatusNotificationService`. Same-named handler classes exist in other services.
- **E3:** `src/ClientApp/Services/Catalog/CatalogService.cs:28,38,53,69,78` (`FilterAsync`, `GetCatalogAsync`, `GetCatalogItemAsync`,
  `GetCatalogBrandAsync`, `GetCatalogTypeAsync`); `src/ClientApp/Services/Order/OrderService.cs:37,51,70,92` (`CreateOrderAsync`, `GetOrdersAsync`,
  `GetOrderAsync`, `CancelOrderAsync`).
- **E4:**
  - `src/ClientApp/Services/Basket/BasketMockService.cs:12,22` (its constructor, field `_mockCustomBasket`);
  - `src/ClientApp/Services/Basket/BasketService.cs:52` (`GetBasketAsync`, `CustomerBasket basket = new();`) and `:93` (`UpdateBasketAsync`, parameter);
  - `src/ClientApp/ViewModels/BasketViewModel.cs:67` (`AddBasketItemAsync`) and `src/ClientApp/ViewModels/CatalogItemViewModel.cs:41` (`AddCatalogItemAsync`),
    both `var basket = await _appEnvironmentService.BasketService.GetBasketAsync()`.
  - `CustomerBasket` is declared in 3 projects.
- **E5:** `src/Ordering.API/Application/Commands/CreateOrderCommandHandler.cs:44` (`Handle`, local `new`),
  `src/Ordering.API/Application/Commands/CreateOrderDraftCommandHandler.cs:16` (`Handle`, `var order = Order.NewDraft()`);
  test `tests/Ordering.UnitTests/Builders.cs:38` (`OrderBuilder.AddOne`, field). `Order` is declared 3 times.

**Reserves**, in substitution order per corpus:

| Id | Symbol | Corpus | Definition | Shape | Grep callers |
|---|---|---|---|---|---|
| J-r1 | `VideoResolver.IsVideoFile` | jellyfin | `Emby.Naming/Video/VideoResolver.cs:119` | static type name (K) | 6 production, 6 files |
| J-r2 | `DirectoryService.GetFile` | jellyfin | `MediaBrowser.Controller/Providers/DirectoryService.cs:78` | `IDirectoryService` parameter (R1 + interface dispatch); must-not `LibraryController.GetFile` | 16 (13 production), 14 files |
| J-r3 | `MetadataResult.AddPerson` | jellyfin | `MediaBrowser.Controller/Providers/MetadataResult.cs:52` | parameter + local `new` (R1, R2); must-not the static `PeopleHelper.AddPerson` | 11 production, 10 files |
| J-r4 | `ProviderManager.QueueRefresh` | jellyfin | `MediaBrowser.Providers/Manager/ProviderManager.cs:1116` | interface field or property (R3 + interface dispatch); 4 overloaded callers | 16 production, 10 files |
| J-r5 | `EventManager.PublishAsync` | jellyfin | `Jellyfin.Server.Implementations/Events/EventManager.cs:37` | interface field (R3 + interface dispatch); the second implementer `NoopEventManager` is in a test file | 10 production, 3 files |
| E-r1 | `OrderingIntegrationEventService.AddAndSaveEventAsync` | eShop | `src/Ordering.API/Application/IntegrationEvents/OrderingIntegrationEventService.cs:36` | interface field (R3 + interface dispatch) | 7 production, 7 files |
| E-r2 | `Order.GetTotal` | eShop | `src/Ordering.Domain/AggregatesModel/OrderAggregate/Order.cs:185` | parameter in `OrderDraftDTO.FromOrder`, an EF-query local in `OrderQueries.GetOrderAsync` | 3 (2 production), 3 files |
| E-r3 | `IdentityService.GetAuthTokenAsync` | eShop | `src/ClientApp/Services/Identity/IdentityService.cs:98` | interface field with 2 production implementers (the AMBIGUOUS rule); another `IIdentityService` exists in Ordering.API | 8 production, 3 files |

**The substitution rule** (step 3 of §8.1; V0 dumps and the Roslyn truth only; before any other variant's dump of a
C# corpus exists). A spot is replaced when, on either engine, any of these holds:
- `Name@file` does not map to exactly one graph node;
- the reference gives it no hop-1 caller;
- its V0 hop-1 recall is already 1 (SATURATED: `impact_spot.py --base` exits 1).

Its replacement is the first reserve of the same corpus, in the order above, that passes the same three checks. A
corpus that runs out of reserves keeps fewer than five spots. Every substitution is recorded in RESULTS_CS.md with
its V0 numbers. Nothing else changes the list.

### 8.4 Variants

| Id | Flags | Role |
|---|---|---|
| V0 | the default (every graph flag unset) | base |
| VT-cs | `DEEPMAP_TYPED_CALLS=csharp` | the candidate (no audit flag: the arm is audit-free, as VT-a was) |
| VT-cs[shape] | VT-cs + `DEEPMAP_TYPED_CALLS_SHAPES=<shape>`, one arm per shape of `typed_calls.SHAPES` (R1 R2 R3 R4 S K M C) | ablation, reported |
| VT-cs+t / VT-cs+np | VT-cs + `DEEPMAP_TYPED_CALLS_TESTS=1` / + `DEEPMAP_TYPED_CALLS_NONPROD=1` | ablations, reported |
| VT-cs-ifs | VT-cs + `DEEPMAP_TYPED_CALLS_IFACE=static` (the interface member only, no implementers) | ablation; draws the implementer stratum (§8.5) |
| VT-cs-if0 | VT-cs + `DEEPMAP_TYPED_CALLS_IFACE=0` (no interface dispatch) | ablation, reported |
| VRC-cs | the exact flip combination, set at decision time with `--set` (for this decision: `DEEPMAP_TYPED_CALLS=csharp` on the RC tree's own defaults) | confirmatory (§8.10) |

- **One table, apart from PREREG's.** `benchmark/call_linking/variants.py` spells these in their own table, apart from
  PREREG §8.3's, so harness gate H's lock on PREREG stays as it is. The harness gate locks the new table to this one.
  Every variant runs in variants.py's clean environment.
- **The inert-variant VOID rule of PREREG §5 applies** (`offline_calls.py` `variant_void`, `delta.py`). Three
  additions:
  - A VT-cs-family dump of a C# corpus whose `call_linking` stats show `csharp` disabled, for any reason (no reader,
    a failed grammar canary, a failed binder, `max_files`), is VOID.
  - The `effective` block reports the interface-dispatch modes through the product, so VT-cs, VT-cs-ifs and VT-cs-if0
    are never an inert pair.
  - If the RC tree has no `DEEPMAP_TYPED_CALLS_IFACE`, VT-cs-ifs and VT-cs-if0 are not run, and §8.5's fallback
    definition of the implementer stratum applies.
- Typed linking is edge-only: every pair compared here must have identical node sets. delta.py and audit_sample.py
  refuse any other pair.

### 8.5 Scoring rules (C#)

- **Mapping.** Truth rows are mapped onto a dump's nodes by the kit's GraphLocator, exactly as for jedi and tsc: the
  innermost defstack entry that has a node is the caller, else the file's Module node; each def maps to the node on its
  line or one of its alt lines.
- **T_s (static pairs):** (caller, callee) from each row's `defs`. **T_d (dispatch pairs):** (caller, callee) from
  each row's `dispatch`, minus T_s.
- **Classification of a graph edge (a, b):**
  - confirmed: (a, b) is in T_s;
  - dispatch-confirmed: it is in T_d;
  - refuted: a has resolved sites (static or dispatch) of b's last name segment, and (a, b) is in neither set;
  - unverified: otherwise.
  The static-only classification (dispatch ignored, as compare_calls does today) is reported beside it, never
  decisive.
- **Receiver forms for G1 (cs):** `name_attr` + `recv` + `this`. This is the js set: C# has `this.` like TypeScript,
  and `base.` (`super`) stays out of it, as js leaves `super` out. A pair counts under its first form in FORM_ORDER
  (`compare_calls`).
- **How the two kinds of edge count:**
  - **An interface-member edge is static.** `_repo.GetAsync(id)` with `_repo` typed `IOrderRepository` binds to
    `IOrderRepository.GetAsync`. The edge to that interface member is in T_s and counts toward G1's recall like any
    static binding.
  - **An implementer or override edge is dispatch.** The edge to `OrderRepository.GetAsync` is in T_d. It never
    enters G1's recall, counts as dispatch-confirmed in G2, is audited in G3 (its own stratum below) and is part of
    the spot reference.
  - **Any other implementer or override edge is a false addition.** In particular, an edge to:
    - a type that, by Roslyn, does not implement the called interface member: a same-named interface of another
      project (eShop's two `IIdentityService`s), or a same-named method that is `new`-hidden or not an override;
    - a declaration outside the corpus's compiled source;
    - a bodiless declaration;
    - for a caller outside test files, a declaration in a test file (a test fake). G2d counts these separately.
- **The implementer stratum (G3b):** the `added` bucket of delta(VT-cs-ifs, VT-cs) on each corpus and engine: exactly
  the implementer edges the interface-dispatch rule adds.
  - Fallback, only if the RC tree has no `DEEPMAP_TYPED_CALLS_IFACE`: the added edges (a, b) of delta(V0, VT-cs) where
    a holds a truth site of b's last name whose static target is an interface member other than b. They are sampled
    like a bucket (seed 20261022, n 40).
- **Library code (G1):** a pair belongs to the library when its CALLER's file is under the corpus's library prefix
  (`compare_calls --src-prefix`).
- **A non-test caller** is one that `_common.caller_class` does not classify as `test`. It uses the product's own
  rule; both corpora keep their test projects under `tests/`.

### 8.6 Layers and commands

**G: graph, offline, deterministic.** Each dump is run twice; the two outputs must be byte-identical (DECISION.md
ruling 1: wall-clock fields stripped).
```
DEEPMAP_HOME=<scratch>/dmhome <flags> <py39 | C:/tmp/cl6-842925/prep/v51/Scripts/python.exe> \
    benchmark/call_linking/offline_calls.py build <CL_WORK>/<id> out/<id>_<V>_<eng>.json --expect-engine graphify
python benchmark/call_linking/truth_roslyn.py <CL_WORK>/<id> out/<id>_truth.json <registered options>   (twice)
python benchmark/call_linking/compare_calls.py out/<id>_truth.json out/<id>_<V>_<eng>.json [--src-prefix=<prefixes>] --json=...
python benchmark/call_linking/compare_calls.py pool <one score per file> --family=cs
python benchmark/call_linking/delta.py out/<id>_<base>_<eng>.json out/<id>_<V>_<eng>.json --truth=out/<id>_truth.json --json=...
python benchmark/call_linking/impact_spot.py --truth=out/<id>_truth.json --corpus=<id> --base=out/<id>_V0_<eng>.json out/<id>_<V>_<eng>.json
python benchmark/call_linking/indegree_drift.py out/<id>_V0_<eng>.json out/<id>_VT-cs_<eng>.json --json=...
```
- Run on both C# corpora × both engines × every §8.4 arm. Every dump is checked for the cross-file EXTRACTED
  invariant (exit 4: the variant fails).
- compare_calls reports for a Roslyn truth: confirmed, dispatch-confirmed, refuted and unverified (the classification
  of §8.5); recall over T_s, overall and per form; dispatch recall over T_d; the AMBIGUOUS and EXTRACTED-refuted
  shares.

**N: non-C# identity.**
- **N corpora:** V0 and VT-cs dumps of each of the 13 N corpora (§8.2), both engines, through the same
  `offline_calls.py build` (twice each).
- **C# corpora:** the edges whose caller's file does not end in `.cs` (case-insensitive) are compared between V0 and
  VT-cs.

**A: audit (blind, two reviewers).**
- **Samples, per C# corpus × engine:**
  - `audit_sample.py sample --corpus=<CL_WORK>/<id> --base=<V0> --variant=<VT-cs> --truth=<truth> --label=<id>_<eng> --seed 20261022 --n 40`:
    the buckets {added, removed, demoted, EXTRACTED}, plus every removed edge the reference confirms, audited
    exhaustively.
  - The implementer stratum: the same command with `--base=<VT-cs-ifs>` and `--label=<id>_<eng>_impl` (same seed
    and n). Only its `added` bucket is scored for G3b; its other buckets are reported.
- **Blind review.** The sheet names no variant, bucket, grade or verdict. Two reviewers each record TRUE, FALSE or
  UNSURE from the call site. Each works alone on a copy of the sheet and never sees the key, a dump, a delta or the
  other reviewer's sheet. A dispute or an UNSURE is neither TRUE nor FALSE.
- **The C# sheet adds one instruction, fixed now:** "C#: the member the compiler binds the call to is TRUE, an
  interface or abstract member included. A call through an interface or a virtual or abstract member is also TRUE for
  a definition with a body, declared in this corpus, that it can run at run time: an implementation or override
  reachable from the receiver's declared type. A definition declared only in test code is FALSE for a caller outside
  test code."
- **The sheet's call-site lines** also match the C# call shapes `M<...>(` and `?.M(`.
- **Precision is reported with Wilson 95% bounds** (`_common.wilson`). Every lower bound in G3 is
  population-weighted over its strata: each stratum's population times its Wilson lower bound when it was sampled
  below its population, or its exact share when it was audited in full (sampled = population: a census has no
  sampling error, the rule audit_sample already applies to its exhaustive stratum), divided by the total population.
  For a pooled bound, Wilson 95% on the summed rows is reported beside it, never decisive. For the removed edges this
  is a new field, `removed_all_exact`, beside the unchanged `removed_all`, so the closed decisions recompute
  identically.
- **An unreliable reference voids its corpus's G deltas.** A reference error rate of 10% or more on a corpus's
  audited rows (rows the reference judged confirmed, dispatch-confirmed or refuted) voids that corpus's G
  confirmed/refuted deltas, and the audit then decides: G2a and G2b hold on that corpus only if G3 holds there. G1
  cannot hold on that corpus, because recall needs a reliable reference.

**E: engine parity**, on both C# corpora:
- `engdiff.py out/<id>_VT-cs_g39.json out/<id>_VT-cs_g51.json --base39=out/<id>_V0_g39.json --base51=out/<id>_V0_g51.json`:
  - the criterion on the edges whose both endpoints exist on both engines (PREREG amendment A1, applied as
    registered there);
  - the raw figures, and the nodes on one engine only, reported beside it;
  - the engine-specific changes.
- `tests/typed_calls_engine_parity_gates.py --corpora`, extended to the two C# corpora:
  - C1: production callers' typed tuples under VT-cs;
  - C2: every caller's typed tuples under VT-cs with `DEEPMAP_TYPED_CALLS_TESTS=1` and `DEEPMAP_TYPED_CALLS_NONPROD=1`
    (the gate's own VTtn mode);
  - the shipped-policy comparison: the typed tuples VT-cs itself produces, test and non-production callers included.

**S: STAGE-0.** `python tests/stage0_refreeze.py --work <fresh> --check --env DEEPMAP_TYPED_CALLS=csharp`, on both
engines.

**R: retrieval, live, matched state.** This layer runs only in VRC-cs (§8.10), for R2. It follows PREREG §8.4 R
unchanged:
- a throwaway home of at most 140 characters, seeded with a copy of the Neo4j archive a new store uses (read from
  `~/.deepmap/cache/`);
- `NEO4J_URI` unset and `HF_HUB_OFFLINE=1`; never `~/.deepmap` and never the base pair 7687/7474;
- edge-only re-syncs: 0 nodes re-embedded and no FTS re-create, else VOID;
- round trips back to V0 must reproduce V0's capture exactly;
- `measure_self.py` with `gt_self65.json`.

**P: performance, from the sync's own records.**
- One throwaway instance per corpus and engine, under the R layer's home rules below:
  `benchmark/clone_graph.py build <clone>` (V0; it embeds once, and that build is not timed).
- Then six edge-only re-syncs, `python build/deepmap_cli.py sync <clone> --stale-cleanup`, alternating V0, VT-cs, V0,
  VT-cs, V0, VT-cs, each with only its arm's flags.
- Each re-sync must log 0 nodes re-embedded and no FTS re-create, else it is VOID and redone.
- Read per run: `sync_wall_s` and `call_linking.seconds` from `.deepmap/graph_build.json`, and the
  `[deepmap sync] typed call linking ...` line.
- The median of the three runs per arm is used. Machine load is recorded beside it, never decisive.
- Offline `<dump>.timing.json` figures are supporting data only (DECISION.md reading 8).

**Kit changes needed before the first RC run.** Each change is gate-locked, and none changes a criterion or
threshold. They are:
- **truth_roslyn** (§8.2) and its harness gate checks (sections A and G, plus a fixture check of forms, defstack,
  overloads, extension calls, interface and override dispatch, and test-file exclusion).
- **Scoring:** `_common.RECEIVER_FORMS["cs"]` and the T_d pairs; compare_calls's C# fields and `pool --family=cs`;
  delta.py's verdict covering dispatch-confirmed.
- **Spots:** impact_spot.py's C# spot table (`Name@file`; the reference T_s ∪ T_d; the exactly-one-node check).
- **Audit:** audit_sample.py's C# sheet sentence, the C# call-site shapes and `removed_all_exact`.
- **Variants and dumps:** variants.py's C# table; offline_calls.py's interface-mode report and its C# VOID rule.
- **Orchestration:** the C# corpora, the N corpora and `self` at the RC commit, in tables apart from those harness
  gate H reads for PREREG.
- **Engine parity:** the C# corpora in the engine-parity gate's `--corpora` mode.
- **The lock:** the harness gate's lock of this block, plus checks that the SHAs, spots and variants here equal the
  kit's tables.
- **The flip guard:** the harness gate's manifest check of §8.7 G5b (inactive while `csharp` is not a default).

### 8.7 Decision D3-cs (VT-cs vs V0; ALL criteria must hold)

D3-cs passes only when every criterion below holds: G1a to G6c, N1 to N3, R1, R2, R3, the drift void rule, S, P1
and P2. Every criterion is judged on each engine end (0.8.39 and 0.8.51) unless it says otherwise. "Per corpus" means
on jellyfin and on eShop, each.

**G1: recall.**
- **G1a:** per corpus × engine, library-code receiver-form recall (T_s; cs forms `name_attr` + `recv` + `this`,
  pooled over those forms) is up by at least **10 points absolute** over V0. The figure pooled over both corpora is
  reported. It cannot be lower than the smaller per-corpus gain, because both arms share one truth.
- **G1b:** no call form's recall falls, on any corpus × engine, whole graph or library prefix, for every form present
  (tolerance 0).
- **G1c:** hop-1 recall improves on **at least 3** of each corpus's final spot symbols (§8.3: five, unless its
  reserves ran out), on each engine. A corpus with fewer than 3 final spots fails G1c.

**G2: graph-wide.**
- **G2a:** confirmed pairs (T_s) ≥ V0, and dispatch-confirmed pairs (T_d) ≥ V0, on every corpus × engine (tolerance
  0).
- **G2b:** the whole-graph refuted share (§8.5's classification) is not higher on any corpus × engine.
- **G2c:** the whole-graph AMBIGUOUS share is not higher on any corpus × engine. It is measured on VT-cs, which sets
  no audit flag: it is already the audit-free arm VT-a was for D3.
- **G2d:** 0 added edges from a non-test caller to a node declared in a test file, on every corpus × engine.

**G3: the audit.**
- **G3a:** audited precision of added edges ≥ 95% pooled per family (dotnet: both corpora on one engine), with a
  lower bound ≥ 90% (§8.6 A), and ≥ 90% per corpus.
- **G3b:** audited precision of the implementer stratum ≥ 95% pooled (lower bound ≥ 90%), and ≥ 90% per corpus. A
  corpus with no implementer addition holds this criterion with nothing to audit; that is reported as empty.
- **G3c:** removed edges audited FALSE ≥ 95% (lower bound ≥ 90%), per corpus × engine, on `removed_all_exact`. A
  corpus × engine with no removal holds it with nothing removed.
- **G3d:** **zero audited-TRUE removals** on any corpus × engine.

**G4: EXTRACTED.**
- **G4a:** EXTRACTED refuted share ≤ 1% on every corpus × engine.
- **G4b:** 0 FALSE in the typed-EXTRACTED audit sample. A row already EXTRACTED in V0 is reported separately and is
  not a typed edge (as DECISION.md read it).
- **G4c:** 0 EXTRACTED edges to a dispatch-only target of an interface call: an edge (a, b) in T_d (so not in T_s)
  where a's site of that name binds statically to an interface member. Interface dispatch is INFERRED at most by
  design.

**G5: gates and the committed effect diff.**
- **G5a:** these gates are GREEN under each engine's Python (graphify 0.8.39; the 0.8.51 venv) on the RC commit:
  - typed_calls_core_gates, typed_calls_py_gates, typed_calls_ts_gates, typed_calls_ts_fuzz_gates,
    typed_calls_push_gates, typed_calls_shapes_gates, typed_calls_engine_parity_gates;
  - typed_calls_cs_gates, typed_calls_cs_fuzz_gates, csharp_members_gates;
  - calls_family_filter_gates, precise_fixes_gates, precise_family_gates, call_linking_harness_gates;
  - the N3 gate.

  The full no-Neo4j suite (`python tests/run_all.py`) must also be GREEN on Windows with 0.8.39 and in a Linux
  container with 0.8.51. A listed gate that does not exist at RC does not pass. Its logs go under
  `benchmark/call_linking/cs/gates/`.
- **G5b:** the on/off effect diff (delta VT-cs vs V0, and delta VT-cs-ifs vs VT-cs) on both C# corpora, both
  engines, is **reviewed and committed**.
  - **Reviewed:** the blind audit of §8.6 A on exactly those pairs, plus a summary in RESULTS_CS.md of every bucket by
    receiver form and grade.
  - **Committed** under `benchmark/call_linking/cs/`: the delta JSONs, the audit `key.json` files, both reviewers'
    filled `sheet.csv` files, the scores and the G5a logs.
  - **Never committed:** `sheet.md`, because it quotes corpus source and jellyfin is GPL-2.0 (it re-creates byte for
    byte from the seed and the committed inputs); dumps and truth files (their sha256 go in RESULTS_CS.md).
  - **How "committed" is established.** The operator commits; the RC run never waits for a commit (§1). The files are
    placed under `benchmark/call_linking/cs/` before DECISION_CS.md is written, with a manifest
    (`cs/MANIFEST.sha256`: one `<sha256>  <relative path>` line per file, byte order of the paths) whose own sha256
    DECISION_CS.md records.
  - A harness gate fails whenever `csharp` is in `_TYPED_DEFAULT_LANGS` and any manifest file is missing or differs,
    or the manifest's sha256 is not the one DECISION_CS.md records. So no commit can carry the flip without these
    files.
  - G5b holds only when that manifest exists, DECISION_CS.md records its sha256, and the gate is GREEN on the tree
    that would carry the flip.

**G6: engine parity.**
- **G6a:** E holds on both C# corpora: VT-cs's symmetric difference between the engines, on the common endpoints,
  is ≤ V0's (tolerance 0).
- **G6b:** the typed tuples are identical across the engines on version-stable keys (`label:name@file:line`), in all
  three comparisons of §8.6 E: C1, C2, and the shipped-policy comparison (test and non-production callers included).
  No accepted list exists, and none can be added: the PRE_RC_DECISIONS.md route needs pilot data, and the first
  DeepMap dump of a C# corpus is RC data. Any differing tuple fails.
- **G6c:** engdiff's engine-specific changes for VT-cs (an edge added, removed or regraded against V0 on one engine
  only, between common endpoints) = 0 on both C# corpora.

**N: non-C# identity.**
- **N1:** on each of the 13 N corpora × engine, the VT-cs dump's `nodes`, `edges`, `counts` and `invariant` are
  byte-identical (the kit's serialisation) to V0's, and neither dump is VOID.
  - This applies to every N corpus that holds no git-tracked `.cs` file: the 12 pinned corpora hold none.
  - An N corpus that holds one at RC is judged by N2's rule instead, and RESULTS_CS.md names its `.cs` files. `self`
    is such a corpus: the RC snapshot holds `benchmark/call_linking/truth_roslyn/Program.cs` (§7).
- **N2:** on each C# corpus × engine, every edge whose caller's file is not `.cs` is identical (pair and grade) in
  VT-cs and V0.
- **N3:** a registered gate shows, on both engines, that `DEEPMAP_TYPED_CALLS=csharp`:
  - leaves the push output of typed_calls_push_gates's corpus (no `.cs` file) byte-identical to the flags-off run;
  - leaves every non-`.cs` caller's tuples of a mixed-language fixture unchanged.

  RESULTS_CS.md names the gate.

**R and S: retrieval and STAGE-0.**
- **R1: vacuous.** benchmark/questions.yaml has no C# question (49 questions on 9 repositories, §8.2), so no atom can
  be lost. DECISION_CS.md reports R1 as "vacuous", never as "measured".
- **R2, R3 and the drift void rule are judged through N1.** They read only non-C# corpora (`self`, the 9 question
  repositories), and an identical graph cannot move them: §8.6 of PREREG, "R in matched state is exact".
  - If N1 fails on any of those corpora, they do not hold.
  - The RC's `self` tree holds a `.cs` file (§7), so N1 does not cover it: on `self`, R2 and R3 are measured live as
    PREREG §8.4 R states them, before DECISION_CS.md is written, with the thresholds of PREREG §8.5 R2 and R3. The
    same rule applies to any other N corpus judged by N2.
  - R2 is also re-measured live in VRC-cs.
- **Drift is always reported** on both C# corpora × engines (count, distribution, maximum multiplier, top-20). The
  void rule as PREREG §8.5 states it: the flip is void if a node whose arm-3 multiplier rose 2× or more becomes
  newly fused-top-3 for 3 or more questions, unless the audit shows its new callers are true production callers. It
  cannot fire on the C# corpora, which have no questions.
- **S:** the `self` oracle and `self-graphify-build` both PASS, on both engines, with **no divergence at any rank**.
  Neither corpus held a `.cs` file at registration, so a divergence means typed linking reached code it should not.

**P: performance.**
- **P1:** sync wall time (`sync_wall_s`) of VT-cs ≤ 1.20 × V0's, on jellyfin and on eShop.
- **P2:** the pass alone (`call_linking.seconds`) ≤ **20 s** on jellyfin.

Both bounds apply on both engines, to the median of 3 (§8.6 P). The logic is PREREG's P:
- **The relative bound:** PREREG bounds the whole sync relatively (+20%) on its two largest corpora; here that is
  both C# corpora.
- **The absolute bound:** PREREG bounds the pass absolutely on the largest (15 s on this repository, 237,161 lines of
  Python by `git ls-files '*.py'` piped to `xargs cat | wc -l`, 2026-10-28). Here that is jellyfin, 317,661 lines of
  C# by the same count: 15 s times the line ratio 1.339 is 20.09 s, rounded to the nearest second, 20 s.

**Readings registered now** (DECISION.md had to choose these after data; here they are fixed before it):
1. **Scope.** G, A and E criteria are judged on the two C# corpora; N on the N corpora and the non-C# callers of the
   C# corpora. The live criteria are judged as R and S above.
2. **G3 granularity.** A G3 bullet that names no pooling is judged per corpus × engine. "Pooled per family" is both
   C# corpora on one engine. Every lower bound is the population-weighted one of §8.6 A.
   - **A stratum audited in full counts at its exact share.** A census has no sampling error, so a Wilson bound on it
     measures nothing. This departs from the reading DECISION.md applied to D3-py's removals (reading 2: requests
     18/18, LB 0.824). It is registered here before any C# data exists, and it changes no closed decision:
     `removed_all` keeps its old computation, and `removed_all_exact` is the new field.
   - It is not a loosening of G3c: G3d (zero audited-TRUE removals) still fails D3-cs on a single TRUE removal.
3. **Hit@15 (VRC-cs R2)** is the net drop `measure_self.compare` computes (`hit15_net_drop <= 1`).
4. **S** needs no attribution reading: no divergence is allowed.
5. **G6** is read strictly, in its three parts.
6. **G5:** both halves must be established when DECISION_CS.md is written.
7. **P** is the sync's own records of §8.6 P; offline timings never establish it.
8. **Any criterion that cannot be computed exactly does not hold.** Where two readings remain, the strict one wins.

### 8.8 Noise floors

- **G, N and E are deterministic** (noise 0). So is the reference: two truth runs are byte-identical.
- **A:** the only randomness is the seeded draw (seed 20261022), and the bounds of §8.6 A cover it.
- **R in matched state is exact** (PREREG §8.6). A void run's diagnosis may use PREREG's cross-history floors; they
  are never slack.
- **P:** the median of 3, with machine load recorded. No slack is added to the 20% or 20 s bounds.
- **No fresh-build comparison is used:** VT-cs changes no node text.

### 8.9 Results that mean DO NOT SHIP

- Any audited-TRUE removal (G3d).
- Any non-C# difference (N1, N2): here the counterpart of an R1 atom loss.
- Self MRR below V0 (VRC-cs, or D3-cs when R2 is measured live).
- EXTRACTED refuted above 1% on either C# corpus × engine.
- G1a below +10 points on any corpus × engine.
- Engine parity worse than V0 (G6a) on either C# corpus.
- A voided run that cannot be made exact.
- The `self` oracle or `self-graphify-build` moving.
- The drift void rule firing.
- **P budgets exceeded.** Optimise, then re-measure P only, on a tree whose G dumps equal the RC's byte for byte. A
  tree that changes any dump is new code: it needs a new registration.
- The C# reader's grammar canary failing under either engine or in the Linux container.
- G3 failing on audit when the reference resolver is unreliable.
- An added edge from a non-test caller to a test-file node (G2d).
- An EXTRACTED edge across files (offline_calls exit 4), or to a dispatch-only target of an interface call (G4c).

### 8.10 The confirmatory run (VRC-cs), the flip, and the fallback

**VRC-cs.** It runs only after D3-cs passes, once, on both engines, on the RC tree, with
`variants.py run VRC-cs --set DEEPMAP_TYPED_CALLS=csharp -- ...`. It re-runs:
- **G:** both C# corpora, from fresh copies and a fresh `DEEPMAP_HOME`. Every dump must be byte-identical to the
  D3-cs VT-cs dump of its corpus × engine (with the wall-clock fields, the `code` stamp and `corpus.root` removed).
- **N:** N1 and N2.
- **R2 (live):** `self`, two fresh replicas; MRR ≥ V0 (tolerance 0) and hit@15 net drop ≤ 1.
- **S:** `stage0_refreeze --check --env DEEPMAP_TYPED_CALLS=csharp`: no divergence.
- **P:** as registered (§8.6 P), with machine load recorded.

R1 has nothing to run (vacuous).

If `build/*.py` changed after §1 was filled (DECISION.md ruling 2's case), VRC-cs runs on the release tree that
would carry the flip. Its first step is a graph-identity attestation: on both engines, the V0 and VT-cs dumps of both
C# corpora and of the N corpora must equal the RC dumps (with the same fields removed). If the attestation fails,
nothing flips; a flip then needs a new registration.

If the same release flips another registered decision too (for example PREREG's D0, the cross-family filter),
VRC-cs is the combination of every flag that release flips, and it re-runs the union of both registrations'
confirmatory layers.

If VRC-cs fails, nothing flips: attribute and re-register.

**The flip** happens only if D3-cs and VRC-cs both pass:
- `csharp` joins `build/typed_calls.py` `_TYPED_DEFAULT_LANGS`. The defaults lock in `tests/typed_calls_core_gates.py`
  and `sync_status`'s knob lists change with it.
- The C# impact note and the copy switch to the linked wording together: README.md, docs/product-page/index.html,
  skills/deep-map/SKILL.md, commands/impact.md, docs/known-limitations.md and public/docs/known-limitations.md, with
  `reliability_copy_gates` and `copy_honesty_scan` run.
- CHANGELOG records a default-graph change for C# repositories.
- Unchanged: `_AUDIT_DEFAULT_LANGS`, `graphify_extract._PRECISE_DEFAULT_FAMILIES`, `DEEPMAP_GRAPHIFY_CALLS_PLUS`,
  Python / TypeScript / JavaScript typed linking, and `DEEPMAP_TYPED_CALLS=1` (wave 1 only).

**The fallback** applies if D3-cs or VRC-cs fails:
- `csharp` stays out of `_TYPED_DEFAULT_LANGS`, and C# typed linking stays opt-in (`DEEPMAP_TYPED_CALLS=csharp`).
- A graph built without it keeps the C# impact note's not-linked wording.
- docs/known-limitations.md and public/docs/known-limitations.md name the opt-in and every failed criterion with its
  measured value.
- DECISION_CS.md records "What would change these outcomes".
- **No partial flip:** no shape, interface-dispatch mode, corpus or engine subset flips anything. Any route forward
  is a new registration, written before its data.
<!-- PREREG-CS-PROTOCOL END -->
