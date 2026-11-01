#!/usr/bin/env python3
"""The `global using` directives MSBuild adds to a C# project's compilation (DEEPMAP_TYPED_CALLS=csharp; opt-in).

A .NET SDK project compiles one more file than the tree holds: GlobalUsings.g.cs, which the SDK's GenerateGlobalUsings
target writes from the project's `Using` items. Those items are the SDK's implicit usings (when the ImplicitUsings
property is `enable` / `true`) and every `<Using Include="N" />` (`Alias="X"`, `Static="true"`) item the project, its
Directory.Build.props and its Directory.Build.targets declare, minus their `<Using Remove="N" />` items. The C# binder
(build/receiver_facts_csharp.py, CsBinder.msbuild) reads them here, once per .csproj group, so a simple type name a
file writes without a using of its own is looked up where the compiler looks it up.

read_group(root, group_dir) -> MsbUsings. The reading is CERTAIN only where every input that decides it was read;
whatever it cannot read goes to `possible` / `alias_unknown` and clears `complete` (a wrong `certain` namespace is the
one way this module could make the core prove a wrong sameness, so certainty is claimed only under a full reading):

  1. Projects: every *.csproj directly in group_dir. None -> the empty, complete reading (a file no project claims gets
     no implicit usings; whether no project claims it is the binder's question: CsBinder.cu_reading). Two or more ->
     each is read; certain = what every one adds, the rest possible, incomplete (`two_projects`: a file there is
     compiled under each).
  2. The nearest Directory.Build.props, the nearest Directory.Packages.props right after it (the end of
     Microsoft.Common.props, through NuGet.props: before the SDK's items) and the nearest Directory.Build.targets (the
     project's directory, then each ancestor up to root; the name compared case-insensitively). NuGet.props decides on
     the values at that point, so a project body's value comes too late: ImportDirectoryPackagesProps set to anything
     but `true` (case-insensitive, untrimmed; empty is the default, true) imports no file; DirectoryPackagesPropsPath
     names the file instead of the nearest, and a virtual absolute path to a file that does not exist imports none.
     ManagePackageVersionsCentrally has no say (all measured, SDK 8.0.202). A Directory.Build.props / .targets /
     .Packages.props found only in another letter case is a TAINT at its place (`case_name`, R39: a case-sensitive
     file system does not import it, and may import another one above). A relative DirectoryPackagesPropsPath
     imports nothing here (NuGet.props's <Import> reads it against the SDK's own directory) and is read as an unread
     input, like an unreadable switch or a path outside root. A Directory.Packages.props of PackageVersion /
     GlobalPackageReference items and properties costs nothing; anything else in it is evaluated like any file.
     Their and the project's <Import Project>: a literal relative path, $(MSBuildThisFileDirectory)..., and
     $([MSBuild]::GetPathOfFileAbove(file[, start])) / $([MSBuild]::GetDirectoryNameOfFileAbove(start, file)), read in
     place; under a condition this module cannot read, the file is still read, everything in it only maybe happening.
     An input that cannot be read (any other target, one outside root or missing; the nearest Directory.Build.* or
     Directory.Packages.props only above root, `props_above_root`) is a TAINT at its place: it may set any property
     (every value read before it, and every property never set, becomes unreadable) and remove any earlier item (every
     item before it only possible): incomplete (`import_unread`). A file that sets a property naming or switching off
     Directory.Build.props / .targets (_DIR_BUILD_SWITCHES) or a Custom{Before,After}Microsoft{Common,CSharp}{Props,
     Targets} / Custom{Before,After}DirectoryBuild{Props,Targets} hook (_IMPORT_HOOK), a project that writes
     anything before its own <Import Project="Sdk.props" Sdk=...> (that part precedes Directory.Build.props, which
     this order does not model), and (R35) a Directory.Build.rsp in the project's directory or above (its -p:
     switches are global properties: `rsp`), a <Project TreatAsLocalProperty=...> in any file read (a global
     property made local: `local_property`) and an Sdk attribute or <Sdk> element in an imported file (an SDK
     imported at a place this order does not model: `sdk_import`) are such a taint at the very end: nothing stays
     certain. A file imported twice is read once (MSBuild ignores the second import, MSB4011).
  3. SDKs: <Project Sdk="A;B/version">, <Sdk Name="...">, <Import Sdk="...">. The implicit-using lists of SDK
     8.0.202's own props files: Microsoft.NET.Sdk.CSharp.props (the seven of Microsoft.NET.Sdk), Sdk.Server.props
     (Microsoft.NET.Sdk.Web's nine more), Microsoft.NET.Sdk.Worker.props (four more),
     Microsoft.NET.Sdk.BlazorWebAssembly's Sdk.props (three more); Microsoft.NET.Sdk.Razor adds none. Any other SDK
     name (Aspire.AppHost.Sdk, MSTest.Sdk, a workload's): the base seven only possible, incomplete (`sdk_unknown`: it
     may add its own). UseMaui (`maui`), UseWindowsForms (`windows_desktop`: the WindowsDesktop targets add items)
     and UseWPF (`windows_desktop`: its targets also remove items, so every item is only possible) true or
     unreadable: incomplete. <Import Project="Sdk.props" Sdk=...> counts as that SDK only beside <Import
     Project="Sdk.targets" Sdk=...>: one half alone (Microsoft.NET.Sdk.CSharp.props, which holds the items, comes with
     the props; GenerateGlobalUsings with the targets) leaves its lists possible (`import_unread`), and with no whole
     SDK every Using item only possible (there may be no GenerateGlobalUsings at all). No SDK at all (an old-style
     project: no GenerateGlobalUsings target): nothing is added, <Using> items ignored, and the reading is complete
     (unless an unreadable <Import> names an SDK path).
  4. Properties: the last assignment wins, in MSBuild's order (props, the project, targets; each file's imports in
     place). All the values one project holds are bounded too (_MAX_HELD: `value_cap`, a value over it unreadable).
     Global properties: Configuration and Platform, both UNREADABLE (R38: `dotnet build` defaults to Debug and passes no
     Platform, a model pinning Release is no evidence: a condition reading either is possible, never certain) and, for a
     multi-targeting project (TargetFrameworks set, TargetFramework not), TargetFramework = the first of
     TargetFrameworks (a second pass with it: MSBuild's inner build). A single-target project has no inner build:
     TargetFramework is an ordinary property there. $(P) expands only a P these files set, a global property or one of
     MSBuild's reserved MSBuildThisFile* / MSBuildProject* names (and AssemblyName, defaulted to the project name, when
     items are read); any other reference (the environment, an SDK-defined property) makes that value or condition
     unreadable, except DefaultItemExcludes, DefaultExcludesInProjectFolder and DefaultItemExcludesInProjectFolder,
     which the SDK sets only in its .targets (after every file read here): unset, they read "". An assignment under an
     unreadable condition makes the property unreadable (unless it already held that value). A value is kept VERBATIM
     and ESCAPED, surrounding whitespace and newlines included, as MSBuild keeps it: only an item list's `;` split
     trims, and %XX is decoded where the value is used (a condition, a path, ImplicitUsings) or, for an item spec, AFTER
     the split on `;` (R36: `%3B` is no separator). A value over 64 KB (literal, or grown by $(P) references) is
     unreadable (`value_cap`, incomplete).
  5. Conditions: '...' == '...', !=, and, or, !, parentheses, a bare word, compared case-insensitively and without a
     trim (MSBuild compares strings so), in three-valued logic (`false and <unreadable>` is false). R34: two
     boolean-like operands (true / on / yes / !false / !off / !no against false / off / no / !true / !on / !yes, any
     case) compare as booleans; a lone operand must be boolean-like (else unreadable). Anything else (Exists(),
     HasTrailingSlash(), $([...]), @(...), %(...), <, >, two numbers spelled differently) is unreadable. An unreadable
     ImplicitUsings: the SDK lists possible (`implicit_unread`). An unreadable condition or value on a <Using Include>:
     its namespaces possible (`using_unread`; an alias item's name to alias_unknown, `alias_unread` when the alias name
     itself is unreadable; a static item: `static_unread`). On a <Using Remove>: every namespace it could name moves
     from certain to possible (`remove_unread`). <Using Update> (`update_unread`), a <Choose> (its branches read under
     unreadable conditions), a <Using> inside a <Target> (`target_unread`: run after evaluation, applied last,
     unreadable) or under an <ItemDefinitionGroup> (`itemdef_unread`: every item only possible) never make anything
     certain. R33: a Remove / Exclude / Update naming a path (`/`, a backslash, `..`, a leading or trailing `.`, a
     reserved path property: MSBuild matches it as a path, which is not modelled) is unread: a Remove's every earlier
     item only possible, an Exclude's Include possible, an Update's every item possible.
  6. ImplicitUsings is on iff its final (decoded) value is `enable` or boolean-true-like (`true`, `on`, `yes`,
     `!false`, `!off`, `!no`; case-insensitive, whitespace NOT trimmed: the SDK's own condition `== 'true' or ==
     'enable'` under R34). Items in MSBuild's order: Directory.Build.props items, Directory.Packages.props items, then
     the SDK list, then the project's items, then Directory.Build.targets items; a <Using Remove="N"> removes every
     EARLIER item of exactly that name, and makes one equal only case-insensitively possible (R39: Windows removes it, a
     case-sensitive file system does not; an Exclude alike), so one in Directory.Build.props removes nothing of the
     SDK's. A `*`
     or `?` in a Remove is a glob on item identities: every earlier item it may match (any item its literal head
     begins) only possible (`remove_unread`). In an Include it is a glob on FILES (the item is `using_unread`), in an
     Exclude its Include's condition unread. Include lists split on `;`; Alias / Static metadata from attributes or
     child elements (Static="true" makes a `global using static`). An Include written `global::N` imports no N (the
     task writes `global using global::global::N;`, measured): an unread item (`using_unread`). System.Net.Http
     carries the SDK's own condition (TargetFrameworkIdentifier != .NETFramework): certain only when every framework
     the project builds for is known not to be .NET Framework (net5+ / netN.M / netcoreapp / netstandard, _tfm_kind),
     dropped when all of them are, and only possible (`framework_unread`) when a framework is unknown, unreadable or
     missing, when the frameworks mix, or when a file sets TargetFrameworkIdentifier itself.
  7. Never raises. A malformed file, one with a DOCTYPE / ENTITY declaration or one over 4 MB: the empty reading,
     incomplete (`xml`); a listing or a read that fails: `unreadable`. `read` / `listdir` are injection seams.
  8. packages: every id a PackageReference / PackageVersion / GlobalPackageReference / Reference names (Include or
     Update; a Reference's text stops at the first comma), read as TEXT from every file read for the group and from a
     packages.config in the group directory: whatever its condition, in a comment too, and from a file the evaluation
     gave up on (an id only ever costs a proof, CsBinder.ns_info); a value with an unexpanded $(...) is skipped. It
     names the assemblies the project takes external types from; it decides nothing here.
  9. links: where a <Compile Include> (conditions other than a proven false; in a <Target> too) reaches outside the
     project's directory, relative to the PROJECT as MSBuild reads it, whichever file wrote it: a repo-relative file, a
     directory prefix ending in "/" (the path before its first wildcard), or "" anywhere (an absolute path, a value
     with an unreadable $(...) / @(...)). A path that leaves root and does not come back: ".." (a file of no tree
     DeepMap reads; it names no file here). A .projitems shared project is an <Import>, so its Compile items arrive
     here. CsBinder.cu_reading uses them (a file another project also compiles sees that project's usings too), and
     CsBinder.linked_in (what a project links in says at assembly level is the project's own).
 10. names (R32): the project file's stem and the final, readable AssemblyName and RootNamespace: the namespace roots
     the repository's own assemblies are named after (CsBinder.ext_root).
 11. compile (R25): which .cs files under the project's directory it compiles, as ops compile_state(msb, group, rel)
     replays: ("base", None, True) when the SDK's default `**/*.cs` glob is provably on (EnableDefaultItems /
     EnableDefaultCompileItems unset, empty or boolean-true; every SDK known and whole), None when not (off: R25 reads
     the explicit list as unread too), False for an old-style project (no SDK: no default glob); then the default
     excludes a file sets (DefaultItemExcludes and kin: their own patterns, the SDK's bin / obj ones not read); then
     every in-directory <Compile Include> and every <Compile Remove> AFTER the default glob (the project's,
     Directory.Build.targets', a Target's; a Directory.Build.props one precedes the glob and removes nothing of it:
     measured), in order, each (op, root-relative glob, certain). A path is placed relative to the project (`**`, `*`,
     `?`; a segment MSBuild would trim, an absolute path, `..` after a wildcard: unplaceable, None); an unread input
     at an item's place is ("any", None, False). compile_state: True / False only for a match with the same letters
     under a certain op, None for a case-only match (R39), an uncertain or unplaceable op. compile_unread: any of it
     not read (it does not clear `complete` here: CsBinder.msbuild does, when a maybe-compiled file writes a global
     directive).
 12. pkgsig (R37): frozenset of (lower-cased id, version, asset metadata) of the project's PackageReference /
     GlobalPackageReference items (a version from the item, its VersionOverride, else the PackageVersion read for the
     group) over its ProjectReference closure (each referenced project evaluated with its own files, paths matched with
     the same letters), or None: a package item, version, asset metadata or condition this module cannot read, an
     Update / Remove of one, a Target or ItemDefinitionGroup holding one, an unread input (a taint), or a
     ProjectReference it cannot place or read (then also `projref_unread`: incomplete). R45 (measured against real
     MSBuild, the c6 packages k01-k18): two COMPLETE readings of DIFFERENT projects compare (the core's U3) only when
     both pkgsig are the EMPTY frozenset: no package anywhere in the project or its readable closure. Equal non-empty
     sets do not suffice: build vs buildTransitive assets, PrivateAssets, framework-specific assets, a package's
     condition, ReferenceOutputAssembly and the central-management switches all change what a package's props and
     targets add, and a package's .targets may also REMOVE usings or switch ImplicitUsings off (k06-k08).

Not read (documented residuals): global usings a NuGet package's build/ props add (xunit-style), and usings its
.targets remove (or ImplicitUsings it switches off): an empty pkgsig is the only proof that none does (R45). Package
ROOTS name the assemblies only by their id's first segment (a package whose namespaces start
otherwise, under a root a project's assembly is named after, stays unseen). Compile items an unread import declares
(their links; their removals make compile_state None). A multi-target project's items under a condition on
TargetFramework are the first framework's. The SDK's own default excludes (bin/, obj/, dot-directories, a Web SDK's
node_modules/) are not modelled: such a file reads compiled.

Stdlib only; imports nothing of DeepMap's.
"""
from __future__ import annotations

import os
import posixpath
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from typing import NamedTuple


class MsbUsings(NamedTuple):
    certain: frozenset          # namespaces MSBuild surely adds as `global using N;`
    possible: frozenset         # namespaces it may add
    aliases: dict               # {alias: target}: `global using X = T;` (certain alias items only)
    alias_unknown: frozenset    # alias names an unreadable or uncertain item may define
    statics: tuple              # `global using static T;` (certain), in item order
    complete: bool              # every input read: `certain` is the whole MSBuild set
    reasons: tuple              # why not complete (sorted words; empty when complete)
    packages: frozenset = frozenset()  # every package id the group's files name (text; docstring 8)
    links: frozenset = frozenset()     # where its <Compile> items reach outside the project directory (docstring 9)
    names: frozenset = frozenset()     # the project's AssemblyName / RootNamespace / file stem (docstring 10)
    compile: tuple = ()                # (op, root-relative glob, certain) of its in-directory Compile items (11)
    compile_unread: bool = False       # a Compile item or the default glob it could not place (docstring 11)
    pkgsig: object = frozenset()       # its packages over the ProjectReference closure, None unreadable (12)


EMPTY = MsbUsings(frozenset(), frozenset(), {}, frozenset(), (), True, ())

# SDK 8.0.202's implicit-using lists: Microsoft.NET.Sdk.CSharp.props (every SDK-style C# project), Sdk.Server.props
# (Microsoft.NET.Sdk.Web), Microsoft.NET.Sdk.Worker.props, Microsoft.NET.Sdk.BlazorWebAssembly's Sdk.props.
USINGS_BASE = ("System", "System.Collections.Generic", "System.IO", "System.Linq", "System.Net.Http",
               "System.Threading", "System.Threading.Tasks")
USINGS_WEB = ("System.Net.Http.Json", "Microsoft.AspNetCore.Builder", "Microsoft.AspNetCore.Hosting",
              "Microsoft.AspNetCore.Http", "Microsoft.AspNetCore.Routing", "Microsoft.Extensions.Configuration",
              "Microsoft.Extensions.DependencyInjection", "Microsoft.Extensions.Hosting",
              "Microsoft.Extensions.Logging")
USINGS_WORKER = ("Microsoft.Extensions.Configuration", "Microsoft.Extensions.DependencyInjection",
                 "Microsoft.Extensions.Hosting", "Microsoft.Extensions.Logging")
USINGS_BLAZOR_WASM = ("Microsoft.Extensions.Configuration", "Microsoft.Extensions.DependencyInjection",
                      "Microsoft.Extensions.Logging")
# per known SDK (lower-cased name): what it adds to the base list (every known SDK adds the base list)
SDK_USINGS = {"microsoft.net.sdk": (), "microsoft.net.sdk.web": USINGS_WEB, "microsoft.net.sdk.worker": USINGS_WORKER,
              "microsoft.net.sdk.blazorwebassembly": USINGS_BLAZOR_WASM, "microsoft.net.sdk.razor": ()}

_MAX_BYTES = 4 * 1024 * 1024
_MAX_VALUE = 64 * 1024            # a property value's length (a file of 24 `$(P)$(P)` lines doubles past 32 MB)
_MAX_HELD = 8 * 1024 * 1024       # all of one project's values together (4 MB of `$(P)` lines at _MAX_VALUE each)
_MAX_IMPORT_DEPTH = 8
_MAX_UP = 64                      # directories walked upward (a guard; a real tree is never this deep)
_PROPS = "directory.build.props"
_TARGETS = "directory.build.targets"
_PACKAGES = "directory.packages.props"
_RSP = "directory.build.rsp"
_SPELLED = {_PROPS: "Directory.Build.props", _TARGETS: "Directory.Build.targets",
            _PACKAGES: "Directory.Packages.props", _RSP: "Directory.Build.rsp"}
_ROOT = "\x00R"                   # a virtual absolute path inside root ("\x00R/dir/file"): never a real path
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_\-]*\Z")
_NUM = re.compile(r"(?:[0-9][0-9.]*|0x[0-9A-Fa-f]+)\Z")
_HEX = re.compile(r"%([0-9A-Fa-f]{2})")
# global properties: Configuration and Platform are read as unreadable (`dotnet build` defaults to Debug and passes no
# Platform; a condition on either is possible, never certain); TargetFramework joins for a multi-target inner build
_GLOBALS = {"configuration": None, "platform": None}
# MSBuild's boolean-like operands (ConversionUtilities): two of them compare as booleans in == / !=
_BOOL_TRUE = frozenset({"true", "on", "yes", "!false", "!off", "!no"})
_BOOL_FALSE = frozenset({"false", "off", "no", "!true", "!on", "!yes"})
# properties the SDK sets only in its .targets (after the project): a file's `$(P)` of one it never set reads ""
_LATE_EMPTY = frozenset({"defaultitemexcludes", "defaultexcludesinprojectfolder", "defaultitemexcludesinprojectfolder"})
# a reserved property that names a path (its value holds the virtual root): a Remove / Exclude / Update naming one
_PATH_PROPS = re.compile(r"\$\(\s*MSBuild(?:ThisFile|Project)(?:Directory|FullPath|DirectoryNoRoot)?\s*\)", re.I)
_PKG_KINDS = frozenset({"packagereference", "globalpackagereference"})
_ASSET_META = ("IncludeAssets", "ExcludeAssets", "PrivateAssets")
_ABSENT = object()                # a property no file set, a metadata no item wrote
# properties that change which Directory.Build.props / .targets the SDK imports (Directory.Packages.props's two
# switches are read exactly: _Proj.choose_packages)
_DIR_BUILD_SWITCHES = ("importdirectorybuildprops", "importdirectorybuildtargets", "directorybuildpropspath",
                       "directorybuildtargetspath")
# properties that import one more file (Microsoft.Common.props / .targets, Microsoft.CSharp.targets)
_IMPORT_HOOK = re.compile(r"custom(?:before|after)(?:microsoft(?:common|csharp)|directorybuild)(?:props|targets)\Z")
_WILD = re.compile(r"[*?]")
_NET_HTTP = "System.Net.Http"     # Microsoft.NET.Sdk.CSharp.props: only when the framework is not .NET Framework
_TFM_FAMILY = re.compile(r"net(?:standard|coreapp)[0-9]+(?:\.[0-9]+)*\Z")
_TFM_NET = re.compile(r"net([0-9]+)((?:\.[0-9]+)*)\Z")
# a tag's attributes: `<` never appears raw in an attribute value, so [^<] spans them, `>` inside a Condition included,
# and each match consumes what it read (linear over a hostile file)
_PKG_ITEM = re.compile(r"<\s*(?:PackageReference|PackageVersion|GlobalPackageReference|Reference)\b"
                       r"([^<]{0,8192})", re.I)
_PKG_ATTR = re.compile(r"""\b(?:Include|Update)\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)
_PKG_CONFIG = re.compile(r"<\s*package\b([^<]{0,8192})", re.I)
_PKG_ID = re.compile(r"""\bid\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)
_PKG_CONFIG_NAME = "packages.config"
_NO_PROJECT_DIRS = frozenset({".git", ".vs", "node_modules", "bin", "obj"})  # a walk for *.csproj skips these
_TOO_BIG = object()
_ABOVE = object()                 # the nearest Directory.Build.* is only above root (unread)
_UNREAD = object()                # the Directory.Packages.props a switch names cannot be told (unread)
_CASE = object()                  # the nearest Directory.Build.* / .Packages.props only in another letter case (R39)
# an input at its place in MSBuild's order that this module does not read: the taint's reason
_MARKS = {_ABOVE: "props_above_root", _UNREAD: "import_unread", _CASE: "case_name"}
_OPERAND = ("str", "word", "unk")


class _Bad(Exception):
    """A file that cannot be read as MSBuild XML: the group's reading is then empty and incomplete."""

    def __init__(self, why):
        super().__init__(why)
        self.why = why


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _attr(el, name):
    """An attribute of el by name, case-insensitive (as MSBuild reads it), or None."""
    low = name.lower()
    for k, v in el.attrib.items():
        if _local(k).lower() == low:
            return v
    return None


def _norm(parts):
    """A "/"-separated root-relative path of path segments ("." and ".." applied), or None when it leaves root."""
    out: list = []
    for p in parts:
        if p in ("", "."):
            continue
        if p == "..":
            if not out:
                return None
            out.pop()
            continue
        out.append(p)
    return "/".join(out)


def _and(a, b):
    """Three-valued and (True / False / None = unreadable)."""
    if a is False or b is False:
        return False
    if a is True and b is True:
        return True
    return None


def _split(spec) -> list:
    return [x.strip() for x in str(spec).split(";") if x.strip()]


def _unesc(s):
    """MSBuild's %XX unescaping (a str; anything else as it is)."""
    return _HEX.sub(lambda m: chr(int(m.group(1), 16)), s) if isinstance(s, str) else s


def _items(spec) -> list:
    """An item spec's entries as MSBuild takes them: split on `;` (trimmed) BEFORE %XX decoding (`%3B` is no
    separator), then each decoded."""
    return [_unesc(x) for x in _split(spec)]


def _boolish(s):
    """True / False for an MSBuild boolean-like string (case-insensitive, untrimmed), else None."""
    if not isinstance(s, str):
        return None
    low = s.lower()
    return True if low in _BOOL_TRUE else (False if low in _BOOL_FALSE else None)


def _pathlike(raw, val) -> bool:
    """R33: does an item spec name a path (MSBuild then matches it as a path, which this module does not model): `/`,
    `\\`, `..`, a leading or trailing `.`, or a reserved path property (raw text or its expanded value)?"""
    if _PATH_PROPS.search(str(raw or "")):
        return True
    for one in [val] + _items(val):
        t = str(one).strip()
        if "/" in t or "\\" in t or ".." in t or "\x00" in t or t.startswith(".") or t.endswith("."):
            return True
    return False


def _tfm_kind(name):
    """A target framework moniker as MSBuild's GetTargetFrameworkIdentifier reads it: "fx" (.NET Framework), "core"
    (.NET, .NET Core, .NET Standard), None (anything else, or not known). `net` + digits is a version whose FIRST digit
    is the major (net48, net10 = 1.0: .NET Framework; net50, net60: .NETCoreApp); with a dot the whole number is
    (net10.0: .NETCoreApp, net4.8: .NET Framework). Measured with `dotnet msbuild` on SDK 8.0.202."""
    head, dash, _plat = str(name).lower().partition("-")
    if _TFM_FAMILY.match(head):
        return "core"
    m = _TFM_NET.match(head)
    if m is None:
        return None
    major = int(m.group(1)) if m.group(2) else int(m.group(1)[0])
    return "core" if major >= 5 else ("fx" if major >= 1 and not dash else None)


def _glob_head(spec) -> str:
    """The literal text every match of wildcard spec begins with ("" for a path: it may match anything)."""
    return "" if "/" in spec or "\\" in spec else spec[:_WILD.search(spec).start()]


def _ids(text, item, attr) -> set:
    """The package ids of `text`: each `item` tag's `attr` value, split on `;`, cut at the first comma (a Reference's
    `Name, Version=...`); a value still holding an unexpanded $(...) / @(...) / %(...) is skipped."""
    out = set()
    for tag in item.finditer(text):
        for hit in attr.finditer(tag.group(1)):
            for one in str(hit.group(1) or hit.group(2) or "").split(";"):
                name = one.split(",", 1)[0].strip()
                if name and "(" not in name:
                    out.add(name)
    return out


# ================================================================================================
# The files
# ================================================================================================

class _Files:
    """Root-relative file access through the injection seams (read / listdir), with parsed-XML caching."""

    def __init__(self, root, read, listdir):
        self.root = str(root or ".")
        self.mapping = None
        self._read = self._list = None
        if isinstance(read, Mapping):
            self.mapping = {}
            for k, v in read.items():
                key = str(k).replace("\\", "/")
                while key.startswith("./"):
                    key = key[2:]
                self.mapping[key] = v.encode("utf-8") if isinstance(v, str) else v
        else:
            self._read = read if callable(read) else None
            self._list = listdir if callable(listdir) else None
        self.docs: dict = {}
        self.lists: dict = {}
        self.above: dict = {}             # file name -> above_root's answer
        self.ids: dict = {}               # rel -> frozenset of the package ids its text names
        self.proj_dirs = None
        self.evals: dict = {}             # project rel -> _Eval (one evaluation per project, shared by the closure)

    def read(self, rel):
        if self.mapping is not None:
            v = self.mapping.get(rel)
            return bytes(v) if isinstance(v, (bytes, bytearray)) else None
        if self._read is not None:
            v = self._read(rel)
            return bytes(v) if isinstance(v, (bytes, bytearray)) else None
        path = os.path.join(self.root, *rel.split("/"))
        try:
            if os.path.getsize(path) > _MAX_BYTES:
                return _TOO_BIG
            with open(path, "rb") as fh:
                return fh.read()
        except (OSError, ValueError):
            return None

    def listdir(self, rel_dir):
        """The file names directly in rel_dir ("" = root), or None when it cannot be listed."""
        if rel_dir in self.lists:
            return self.lists[rel_dir]
        got = None
        try:
            if self.mapping is not None:
                prefix = f"{rel_dir}/" if rel_dir else ""
                names = set()
                for k in self.mapping:
                    if k.startswith("../") or not k.startswith(prefix):
                        continue
                    rest = k[len(prefix):]
                    if rest and "/" not in rest:
                        names.add(rest)
                got = sorted(names)
            elif self._list is not None:
                v = self._list(rel_dir)
                got = None if v is None else sorted(str(x) for x in v)
            else:
                with os.scandir(os.path.join(self.root, *[p for p in rel_dir.split("/") if p])) as it:
                    got = sorted(e.name for e in it if e.is_file())
        except (OSError, ValueError, TypeError):
            got = None
        self.lists[rel_dir] = got
        return got

    def find(self, rel_dir, lname):
        """The root-relative path of the file named lname (lower-case, compared case-insensitively) directly in
        rel_dir, or None; raises _Bad("unreadable") when rel_dir cannot be listed."""
        names = self.listdir(rel_dir)
        if names is None:
            raise _Bad("unreadable")
        hits = sorted(n for n in names if n.lower() == lname)
        if not hits:
            return None
        return f"{rel_dir}/{hits[0]}" if rel_dir else hits[0]

    def resolve(self, rel):
        """rel as spelled on disk (the exact name, else its one case-insensitive match), or None (no such file)."""
        d, n = posixpath.split(rel)
        names = self.listdir(d)
        if not names or not n:
            return None
        if n in names:
            return rel
        hits = sorted(x for x in names if x.lower() == n.lower())
        return (f"{d}/{hits[0]}" if d else hits[0]) if hits else None

    def above_root(self, name):
        """Is there a file `name` in a directory above root (MSBuild's upward lookup would reach it)? Memoised: the
        answer depends on root and name only."""
        if name not in self.above:
            self.above[name] = self._above_root(name)
        return self.above[name]

    def _above_root(self, name):
        if self.mapping is not None:
            low = name.lower()
            return any(k.startswith("../") and posixpath.basename(k).lower() == low for k in self.mapping)
        if self._read is not None:
            return False                 # an injected reader serves the tree only: nothing above it
        try:
            cur = os.path.dirname(os.path.abspath(self.root))
            for _ in range(_MAX_UP):
                if os.path.isfile(os.path.join(cur, name)):
                    return True
                up = os.path.dirname(cur)
                if up == cur:
                    return False
                cur = up
        except (OSError, ValueError):
            pass
        return True                      # not provable either way: read as present (incomplete)

    def package_ids(self, rel) -> frozenset:
        """The package ids file rel's TEXT names (cached): a tag read by pattern, not an element evaluated, so a
        condition, a comment and a file that does not parse all still count. Never raises."""
        if rel not in self.ids:
            out: set = set()
            try:
                data = self.read(rel)
                if isinstance(data, (bytes, bytearray)) and len(data) <= _MAX_BYTES:
                    wide = data[:2] in (b"\xff\xfe", b"\xfe\xff")
                    text = bytes(data).decode("utf-16" if wide else "utf-8", "replace")
                    if posixpath.basename(rel).lower() == _PKG_CONFIG_NAME:
                        out = _ids(text, _PKG_CONFIG, _PKG_ID)
                    else:
                        out = _ids(text, _PKG_ITEM, _PKG_ATTR)
            except Exception:  # noqa: BLE001 — an unreadable file names nothing
                out = set()
            self.ids[rel] = frozenset(out)
        return self.ids[rel]

    def project_dirs(self) -> tuple:
        """Every directory of the tree directly holding a *.csproj, root-relative and sorted (memoised); none for an
        injected reader over a callable (it serves files, not a tree)."""
        if self.proj_dirs is None:
            out: set = set()
            try:
                if self.mapping is not None:
                    out = {posixpath.dirname(k) for k in self.mapping
                           if k.lower().endswith(".csproj") and not k.startswith("../")}
                elif self._read is None:
                    base = os.path.abspath(self.root)
                    for cur, dirs, names in os.walk(base):
                        dirs[:] = [d for d in dirs if d.lower() not in _NO_PROJECT_DIRS]
                        if any(n.lower().endswith(".csproj") for n in names):
                            rel = os.path.relpath(cur, base)
                            out.add("" if rel == os.curdir else rel.replace(os.sep, "/"))
            except (OSError, ValueError):
                pass
            self.proj_dirs = tuple(sorted(out))
        return self.proj_dirs

    def inside(self, parts):
        """Path segments relative to root as a root-relative path; a path that leaves root and comes back through its
        own name is followed on the real path; None: it stays outside the tree."""
        got = _norm(parts)
        if got is not None:
            return got
        try:
            base = os.path.abspath(self.root)
            rel = os.path.relpath(os.path.normpath(os.path.join(base, *parts)), base).replace(os.sep, "/")
        except (OSError, ValueError):
            return ""                         # not decidable: anywhere
        return None if rel == ".." or rel.startswith("../") else ("" if rel == os.curdir else rel)

    def doc(self, rel):
        """The root element of MSBuild XML file rel (cached); raises _Bad."""
        got = self.docs.get(rel)
        if isinstance(got, _Bad):
            raise got
        if got is not None:
            return got
        try:
            data = self.read(rel)
            if data is None:
                raise _Bad("unreadable")
            if data is _TOO_BIG or len(data) > _MAX_BYTES:
                raise _Bad("xml")
            if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
                text = data.decode("utf-16", "replace")
            else:
                text = data.decode("utf-8", "replace")
            if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", text, re.I):
                raise _Bad("xml")             # an MSBuild file has no DTD: never expand one
            try:
                el = ET.fromstring(data)
            except (ET.ParseError, ValueError, UnicodeError, LookupError) as exc:
                raise _Bad("xml") from exc
            if _local(el.tag) != "Project":
                raise _Bad("xml")
            self.docs[rel] = el
            return el
        except _Bad as exc:
            self.docs[rel] = exc
            raise


# ================================================================================================
# One project's evaluation
# ================================================================================================

class _Proj:
    """The evaluation of one .csproj: its properties in MSBuild's order, then its Using items."""

    def __init__(self, files, rel, touched=None):
        self.f = files
        self.touched = touched if touched is not None else set()   # every file read for the group
        self.rel = rel
        self.dir = posixpath.dirname(rel)
        self.name = posixpath.basename(rel)
        self.stem = self.name.rsplit(".", 1)[0] if "." in self.name else self.name
        self.v: dict = {}                 # lower-cased property name -> value (None: unreadable)
        self.glob: dict = dict(_GLOBALS)
        self.this = rel                   # the file being read (MSBuildThisFile*)
        self.items_pass = False
        self.reset_props()
        self.phase_items: dict = {"props": [], "project": [], "targets": []}
        self.target_usings: list = []     # <Using> elements inside a <Target>
        self.itemdef_using = False
        self.seen: set = set()
        self.links: set = set()           # <Compile> reaches outside the project directory (docstring 9)
        self.compile_ops: list = []       # (phase, "inc" | "rem", root-relative glob | None, certain) (docstring 11)
        self.pkgs: list = []              # (id, version, VersionOverride, asset metadata) (docstring 12)
        self.pkg_versions: dict = {}      # lower-cased id -> a PackageVersion's Version (None: unreadable)
        self.pkg_bad = False              # a package item, version or condition this module cannot read
        self.projrefs: list = []          # ProjectReference paths (root-relative), in item order
        self.projref_bad = False          # a ProjectReference this module cannot place

    def reset_props(self):
        self.sdks: list = []              # lower-cased SDK names
        self.maybe_sdks: list = []        # an <Import Sdk> under an unreadable condition, or one half of an SDK
        self.halves: dict = {}            # an <Import Project=... Sdk=name>: name -> the parts imported
        self.packages: list = []          # what the property pass imports as Directory.Packages.props
        self.held = 0                     # the characters of every value assigned so far
        self.sdk_text_unread = False      # an unreadable <Import> whose text names an SDK
        self.reasons: set = set()
        self.imports: dict = {}           # id(<Import>) -> (the file it imports, its condition: True | None)
        self.unread_ids: set = set()      # id(<Import>) of the imports that could not be read (a taint)
        self.tainted = False              # an unread input may have set any property
        self.local_prop = False           # a <Project TreatAsLocalProperty=...> (a global property made local)
        self.sdk_import = False           # an imported file names an SDK (Sdk attribute / <Sdk> element)

    # ---------------------------------------------------------------- expansion
    def reserved(self, low):
        d, tb = posixpath.dirname(self.this), posixpath.basename(self.this)
        if low == "msbuildthisfiledirectory":
            return f"{_ROOT}/{d}/" if d else f"{_ROOT}/"
        if low == "msbuildthisfile":
            return tb
        if low == "msbuildthisfilename":
            return tb.rsplit(".", 1)[0] if "." in tb else tb
        if low == "msbuildthisfileextension":
            return ("." + tb.rsplit(".", 1)[1]) if "." in tb else ""
        if low == "msbuildthisfilefullpath":
            return f"{_ROOT}/{self.this}"
        if low == "msbuildprojectname":
            return self.stem
        if low == "msbuildprojectfile":
            return self.name
        if low == "msbuildprojectextension":
            return ("." + self.name.rsplit(".", 1)[1]) if "." in self.name else ""
        if low == "msbuildprojectdirectory":
            return f"{_ROOT}/{self.dir}" if self.dir else _ROOT
        if low == "msbuildprojectfullpath":
            return f"{_ROOT}/{self.rel}"
        if low == "assemblyname" and self.items_pass:
            got = self.v.get("assemblyname", _ABSENT)
            if got is _ABSENT or (isinstance(got, str) and not got):
                return self.stem          # the SDK's default ($(MSBuildProjectName)), set before items are read
            return got
        return _ABSENT

    def prop(self, low):
        """A property's value for $(P): None when unreadable (never set by these files, or set unreadably)."""
        if low in self.glob:
            return self.glob[low]
        r = self.reserved(low)
        if r is not _ABSENT:
            return r
        got = self.v.get(low, _ABSENT)
        if got is _ABSENT and low in _LATE_EMPTY and not self.tainted:
            return ""                      # the SDK sets it only in its .targets, after every file read here
        return None if got is _ABSENT else got

    def expand(self, s, fileabove=False, decode=True):
        """s with its $(P) references expanded and (decode) %XX escapes decoded; None when unreadable. A property
        holds its value escaped, as MSBuild keeps it: an item list is split on `;` before decoding (decode=False,
        then _items). With fileabove (an Import's Project), the two $([MSBuild]::...FileAbove(...)) functions are
        evaluated."""
        if s is None:
            return ""
        out, i, n, grown = [], 0, len(s), 0
        while i < n:
            ch = s[i]
            if ch in "@%" and i + 1 < n and s[i + 1] == "(":
                return None
            if ch == "$" and i + 1 < n and s[i + 1] == "(":
                j, depth = i + 1, 0
                while j < n:
                    if s[j] == "(":
                        depth += 1
                    elif s[j] == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    j += 1
                if j >= n:
                    return None
                inner = s[i + 2:j].strip()
                if inner.startswith("[") and fileabove:
                    got = self.file_above(inner)
                elif _NAME.match(inner):
                    got = self.prop(inner.lower())
                else:
                    got = None                 # a property function, P.Method(), Registry:...
                if got is None:
                    return None
                grown += len(got)
                if grown > _MAX_VALUE:             # `$(P)$(P)` lines double: never a MemoryError path
                    self.reasons.add("value_cap")
                    return None
                out.append(got)
                i = j + 1
                continue
            out.append(ch)
            i += 1
        return _unesc("".join(out)) if decode else "".join(out)

    def args(self, text):
        """The comma-separated arguments of a call's `(...)`, quotes stripped and expanded; None if unreadable."""
        if not text.startswith("(") or not text.endswith(")"):
            return None
        parts, cur, q, depth = [], [], False, 0
        for ch in text[1:-1]:
            if ch == "'":
                q = not q
            elif not q and ch == "(":
                depth += 1
            elif not q and ch == ")":
                depth -= 1
            elif not q and depth == 0 and ch == ",":
                parts.append("".join(cur))
                cur = []
                continue
            cur.append(ch)
        parts.append("".join(cur))
        out = []
        for p in parts:
            t = p.strip()
            if len(t) >= 2 and t[0] == "'" and t[-1] == "'":
                t = t[1:-1]
            v = self.expand(t)
            if v is None:
                return None
            out.append(v.strip())
        return out

    def file_above(self, inner):
        """$([MSBuild]::GetPathOfFileAbove(file[, start])) / $([MSBuild]::GetDirectoryNameOfFileAbove(start, file)):
        the virtual path (directory) of the nearest such file inside root, "" when there is none anywhere; None when
        unreadable (another function, a start outside root, the nearest one only above root)."""
        m = re.match(r"\[MSBuild\]::(GetPathOfFileAbove|GetDirectoryNameOfFileAbove)\s*(\(.*\))\s*\Z", inner,
                     re.I | re.S)
        if m is None:
            return None
        fn, a = m.group(1).lower(), self.args(m.group(2))
        if a is None:
            return None
        if fn == "getpathoffileabove":
            if len(a) not in (1, 2):
                return None
            fname, start = a[0], (a[1] if len(a) == 2 else self.reserved("msbuildthisfiledirectory"))
        else:
            if len(a) != 2:
                return None
            start, fname = a
        if not fname or any(c in fname for c in "/\\*?"):
            return None
        cur = self.vpath(start)
        if cur is None:
            return None
        for _ in range(_MAX_UP):
            hit = self.f.find(cur, fname.lower())
            if hit is not None:
                if fn == "getpathoffileabove":
                    return f"{_ROOT}/{hit}"
                hd = posixpath.dirname(hit)
                return f"{_ROOT}/{hd}" if hd else _ROOT
            if not cur:
                break
            cur = posixpath.dirname(cur)
        return None if self.f.above_root(fname) else ""

    def vpath(self, s):
        """The root-relative path of expanded path text s (virtual absolute, or relative to the file being read), or
        None when it leaves root or is no plain path."""
        if s is None:
            return None
        t = s.strip().replace("\\", "/")
        if t.startswith(_ROOT):
            t = t[len(_ROOT):]
            if not (t == "" or t.startswith("/")):
                return None
            parts = t.split("/")
        else:
            if t.startswith("/") or re.match(r"[A-Za-z]:", t):
                return None
            parts = posixpath.dirname(self.this).split("/") + t.split("/")
        if any("\x00" in p or "*" in p or "?" in p or "$" in p for p in parts):
            return None
        return _norm(parts)

    # ---------------------------------------------------------------- the walk
    def cond(self, el):
        c = _attr(el, "Condition")
        if c is None or not c.strip():
            return True
        return _Cond(c, self).run()

    def walk(self, props, targets, items, packages=()):
        """One pass over the files in MSBuild's order. props / targets / packages: the nearest Directory.Build.props
        / .targets / Directory.Packages.props ([] none, [_ABOVE] one only above root: an unread input at that place)."""
        self.items_pass = items
        self.seen = set()
        if not items:
            self.reset_props()
        for f in props:
            if f in _MARKS:
                self.taint("props", _MARKS[f])
            else:
                self.read_file(f, "props", 0, False)
        if not items:
            self.packages = self.choose_packages(list(packages))
        for f in self.packages:
            if f in _MARKS:
                self.taint("props", _MARKS[f])
            else:
                self.read_file(f, "props", 0, False)
        self.read_file(self.rel, "project", 0, True)
        for f in targets:
            if f in _MARKS:
                self.taint("targets", _MARKS[f])
            else:
                self.read_file(f, "targets", 0, False)

    def choose_packages(self, nearest) -> list:
        """NuGet.props, at the end of Microsoft.Common.props: which file it imports as Directory.Packages.props, from
        the two switches' values at this point (module docstring 2). The property pass decides; the item pass reads the
        same file (a property set later undoes no import)."""
        named = _unesc(self.prop_final("directorypackagespropspath"))
        if named is None:
            pick = [_UNREAD]
        elif named is _ABSENT or named == "":
            pick = nearest
        elif not named.strip().replace("\\", "/").startswith(_ROOT):
            pick = [_UNREAD]                         # relative or real absolute: measured, it imports nothing here
        else:
            rel = self.vpath(named)
            got = self.f.resolve(rel) if rel else None
            pick = [_UNREAD] if rel is None else ([got] if got else [])
        on = _unesc(self.prop_final("importdirectorypackagesprops"))
        if on is None:
            return [_UNREAD] if pick else []
        return pick if on is _ABSENT or on == "" or _boolish(on) is True else []      # NuGet.props: == 'true'

    def taint(self, phase, why):
        """An input that could not be read, at this place in MSBuild's order: it may set any property (every value
        read so far becomes unreadable, and so does a property never set) and add or remove any earlier item."""
        if self.items_pass:
            self.phase_items[phase].append(("taint", why))
            self.compile_ops.append((phase, "any", None, False))   # it may add or remove any Compile item...
            self.pkg_bad = True                      # ...and any package or project reference
            return
        self.reasons.add(why)
        self.tainted = True
        for k in list(self.v):
            self.v[k] = None

    def read_file(self, rel, phase, depth, is_project, ctx=True):
        """Read file rel's elements; ctx None: imported under a condition this module cannot read (everything in it
        only maybe happens)."""
        if rel in self.seen:
            return                                   # imported twice: MSBuild ignores the second (MSB4011)
        self.seen.add(rel)
        self.touched.add(rel)
        root = self.f.doc(rel)
        prev, self.this = self.this, rel
        try:
            if not self.items_pass:
                if _attr(root, "TreatAsLocalProperty") is not None:
                    self.local_prop = True           # a global property this module pins may be set here (R35)
                sdk_el = any(_local(e.tag) == "Sdk" for e in root)
                if not is_project and (str(_attr(root, "Sdk") or "").strip() or sdk_el):
                    self.sdk_import = True           # an SDK imported from this file (R35): its place is not modelled
            if is_project and not self.items_pass:
                for s in (_attr(root, "Sdk") or "").split(";"):
                    self.add_sdk(s)
                for e in root:
                    if _local(e.tag) == "Sdk":
                        self.add_sdk(_attr(e, "Name") or "")
            for el in root:
                self.element(el, phase, depth, ctx)
        finally:
            self.this = prev

    def add_sdk(self, s, maybe=False):
        n = s.split("/", 1)[0].strip().lower()
        tgt = self.maybe_sdks if maybe else self.sdks
        if n and n not in tgt:
            tgt.append(n)

    def element(self, el, phase, depth, ctx):
        """One child of <Project> (or of a <Choose> branch: ctx None, an unreadable condition)."""
        t = _local(el.tag)
        if t == "PropertyGroup":
            if self.items_pass:
                return
            c = _and(ctx, self.cond(el))
            if c is False:
                return
            for p in el:
                if isinstance(p.tag, str):
                    c2 = _and(c, self.cond(p))
                    if c2 is not False:
                        self.assign(p, c2)
        elif t == "ItemGroup":
            if not self.items_pass:
                return
            c = _and(ctx, self.cond(el))
            if c is False:
                return
            for it in el:
                if isinstance(it.tag, str):
                    self.item(it, self.phase_items[phase], _and(c, self.cond(it)), phase)
        elif t == "Import":
            self.import_(el, phase, depth, ctx)
        elif t == "ImportGroup":
            c = _and(ctx, self.cond(el))
            if c is not False:
                for imp in el:
                    if _local(imp.tag) == "Import":
                        self.import_(imp, phase, depth, c)
        elif t == "Choose":
            for br in el:
                if _local(br.tag) in ("When", "Otherwise"):
                    for k in br:
                        if isinstance(k.tag, str):
                            self.element(k, phase, depth, None)
        elif t == "Target":
            if self.items_pass:
                self.target_usings.extend(u for u in el.iter()
                                          if isinstance(u.tag, str) and _local(u.tag).lower() == "using")
                for u in el.iter():
                    if isinstance(u.tag, str) and _local(u.tag).lower() == "compile":
                        self.compile_item(u, None, "late")
                    elif isinstance(u.tag, str) and _local(u.tag).lower() in _PKG_KINDS | {"projectreference"}:
                        self.pkg_bad = self.projref_bad = True       # run at build time: not read
        elif t == "ItemDefinitionGroup":
            if any(isinstance(k.tag, str) and _local(k.tag).lower() == "using" for k in el):
                self.itemdef_using = True
            if any(isinstance(k.tag, str) and _local(k.tag).lower() in _PKG_KINDS for k in el):
                self.pkg_bad = True                  # default asset metadata: what flows is not read

    def assign(self, p, c):
        low = _local(p.tag).lower()
        if low in self.glob:
            return                                   # a global property is never overwritten
        val = None if len(p) else self.expand(p.text or "", decode=False)   # kept escaped; XML inside: not read
        self.held += len(val) if val is not None else 0
        if val is not None and (len(val) > _MAX_VALUE or self.held > _MAX_HELD):
            self.reasons.add("value_cap")
            val = None
        if c is True:
            self.v[low] = val
            return
        old = self.v.get(low, _ABSENT)
        if old is _ABSENT or old is None or val is None or old != val:
            self.v[low] = None                       # maybe assigned: unreadable from here on

    def import_(self, el, phase, depth, ctx):
        """An <Import>: an SDK; a file inside root, read in place (under an unreadable condition: everything in it
        only maybe happens); anything else unread (a taint at this place, `import_unread`)."""
        if self.items_pass:
            got = self.imports.get(id(el))
            if got is not None:
                self.read_file(got[0], phase, depth + 1, False, got[1])
            elif id(el) in self.unread_ids:
                self.taint(phase, "import_unread")
            return
        c = _and(ctx, self.cond(el))
        if c is False:
            return
        sdk = _attr(el, "Sdk")
        if sdk is not None:
            if c is None:
                self.reasons.add("import_unread")
                self.add_sdk(sdk, maybe=True)
            else:
                part = str(_attr(el, "Project") or "").strip().lower()
                self.halves.setdefault(sdk.split("/", 1)[0].strip().lower(), set()).add(part)
            return
        proj = _attr(el, "Project")
        if proj is None:
            return
        val = self.expand(proj, fileabove=True)
        if val is not None and not val.strip():
            return                                   # a file above that exists nowhere: nothing is imported
        rel = self.vpath(val) if val is not None else None
        got = self.f.resolve(rel) if rel else None
        if got is None or depth >= _MAX_IMPORT_DEPTH:
            if "sdk" in str(proj).lower():
                self.sdk_text_unread = True
            self.unread_ids.add(id(el))
            self.taint(phase, "import_unread")
            return
        self.imports[id(el)] = (got, c)
        self.read_file(got, phase, depth + 1, False, c)

    # ---------------------------------------------------------------- items
    def meta(self, it, name):
        """An item's metadata `name`: its value, None (unreadable) or _ABSENT; a child element wins an attribute."""
        a = _attr(it, name)
        got = _ABSENT
        if a is not None:
            got = self.expand(a)
            got = None if got is None else got.strip()
        for k in it:
            if isinstance(k.tag, str) and _local(k.tag).lower() == name.lower():
                c = self.cond(k)
                if c is False:
                    continue
                v = None if len(k) else self.expand(k.text or "")
                got = None if v is None or c is None else v.strip()
        return got

    def item(self, it, ops, c, phase="project"):
        """A <Using> item -> ops on the item list: ("inc", ns | None, alias, static, certain, why),
        ("rem", {names as written} | None, certain), ("glob", lower-cased literal head of a wildcard Remove),
        ("upd", {lower-cased names} | None). A <Compile> item -> self.links / self.compile_ops; a package or project
        reference -> self.pkgs / self.projrefs. Item specs split on `;` before %XX decoding (R36); a Remove / Exclude /
        Update naming a path is unread (R33); a Remove / Exclude matching only in another letter case: possible
        (R39)."""
        kind = _local(it.tag).lower()
        if kind == "compile":
            self.compile_item(it, c, phase)
            return
        if kind in _PKG_KINDS or kind in ("packageversion", "projectreference"):
            self.pkg_item(it, kind, c)
            return
        if kind != "using" or c is False:
            return
        inc, rem, upd = _attr(it, "Include"), _attr(it, "Remove"), _attr(it, "Update")
        if inc is not None:
            alias = self.meta(it, "Alias")
            alias = "" if alias is _ABSENT else alias            # "" no alias, None unreadable
            st = self.meta(it, "Static")
            static = False if st is _ABSENT else (None if st is None or st.lower() not in ("true", "false", "")
                                                  else st.lower() == "true")
            spec = self.expand(inc, decode=False)
            exc = _attr(it, "Exclude")
            ex: set = set()
            if exc is not None:
                ev = self.expand(exc, decode=False)
                if ev is None or _WILD.search(ev) or _pathlike(exc, ev):
                    c = None
                else:
                    ex = set(_items(ev))
            if spec is None:
                ops.append(("inc", None, alias, static, False, "using_unread"))
                return
            low_ex = {x.lower() for x in ex}
            for ns in _split(spec):
                if _WILD.search(ns):                 # a glob on files: what it adds depends on the directory
                    ops.append(("inc", None, alias, static, False, "using_unread"))
                    continue
                ns = _unesc(ns)                      # R36: decoded after the split (a raw `*` is the glob)
                if not ns or ns in ex:
                    continue
                if ns.startswith("global::"):        # the task writes `global using global::global::N;` (measured,
                    ops.append(("inc", None, alias, static, False, "using_unread"))   # SDK 8.0.202): never N
                    continue
                if ns.lower() in low_ex:             # excluded only case-insensitively (Windows): possible
                    ops.append(("inc", ns, alias, static, False, "case_only"))
                    continue
                ops.append(("inc", ns, alias, static, c is True, "using_unread"))
        elif rem is not None:
            spec = self.expand(rem, decode=False)
            if spec is None or _pathlike(rem, spec):
                ops.append(("rem", None, False))     # unread: any item it may match is only possible
                return
            names = set()
            for raw in _split(spec):
                if _WILD.search(raw):
                    ops.append(("glob", _glob_head(_unesc(raw).lower())))
                else:
                    names.add(_unesc(raw))
            ops.append(("rem", names, c is True))
        elif upd is not None:
            spec = self.expand(upd, decode=False)
            ops.append(("upd", None if spec is None or _WILD.search(spec) or _pathlike(upd, spec)
                        else {x.lower() for x in _items(spec)}))

    def compile_item(self, it, c, phase):
        """<Compile Include / Remove>: where an Include reaches outside the project's directory goes to self.links
        (docstring 9); an in-directory Include and every Remove to self.compile_ops (docstring 11)."""
        if c is False:
            return
        inc, rem = _attr(it, "Include"), _attr(it, "Remove")
        if inc is not None:
            spec = self.expand(inc, decode=False)
            if spec is None:
                self.links.add("")
                self.compile_ops.append((phase, "inc", None, False))
                return
            narrowed = _attr(it, "Exclude") is not None              # an Exclude: which files it adds is not read
            for one in _items(spec):
                path = one.replace("\\", "/")
                got = self.link_of(path)
                if got is not None:
                    self.links.add(got)
                else:
                    self.compile_ops.append((phase, "inc", self.compile_pattern(path),
                                             c is True and not narrowed))
        elif rem is not None:
            spec = self.expand(rem, decode=False)
            if spec is None:
                self.compile_ops.append((phase, "rem", None, False))
                return
            for one in _items(spec):
                self.compile_ops.append((phase, "rem", self.compile_pattern(one.replace("\\", "/")), c is True))

    def compile_pattern(self, path):
        """A Compile Include / Remove path ("/"-separated, decoded, relative to the PROJECT whichever file wrote it)
        as a root-relative glob (`**`, `*`, `?`), or None when it cannot be placed: an absolute path, a path leaving
        root, `..` after a wildcard, a segment MSBuild would trim (a trailing `.` or space) or holding `$` / `@` / `%`
        or `**` beside other text."""
        if path.startswith(_ROOT):
            parts = path[len(_ROOT):].split("/")
        elif path.startswith("/") or re.match(r"[A-Za-z]:", path):
            return None
        else:
            parts = self.dir.split("/") + path.split("/")
        wild = False
        for s in parts:
            if s in ("", "."):
                continue
            if s == "..":
                if wild:
                    return None
                continue
            if s != s.strip() or s.endswith(".") or any(ch in s for ch in "$@%\x00;") or ("**" in s and s != "**"):
                return None
            wild = wild or bool(_WILD.search(s))
        return _norm(parts)

    def pkg_item(self, it, kind, c):
        """PackageReference / GlobalPackageReference -> self.pkgs, PackageVersion -> self.pkg_versions,
        ProjectReference -> self.projrefs (docstring 12); what this module cannot read sets pkg_bad / projref_bad."""
        if c is False:
            return
        inc, upd, rem = _attr(it, "Include"), _attr(it, "Update"), _attr(it, "Remove")
        if kind == "projectreference":
            spec = self.expand(inc, decode=False) if inc is not None else None
            if spec is None or c is not True:
                self.projref_bad = True              # a Remove / Update, an unreadable path or condition
                return
            for raw in _split(spec):
                path = _unesc(raw).replace("\\", "/")
                rel = None if _WILD.search(raw) else self.compile_pattern(path)
                if rel is None:
                    self.projref_bad = True
                else:
                    self.projrefs.append(rel)
            return
        spec = self.expand(inc, decode=False) if inc is not None else None
        if spec is None or c is not True or upd is not None or rem is not None:
            self.pkg_bad = True                      # an Update / Remove, an unreadable id or condition
            return
        if kind == "packageversion":
            ver = self.meta(it, "Version")
            for raw in _split(spec):
                self.pkg_versions[_unesc(raw).lower()] = None if ver is None else ("" if ver is _ABSENT else ver)
            return
        ver, over = self.meta(it, "Version"), self.meta(it, "VersionOverride")
        assets = tuple(self.meta(it, m) for m in _ASSET_META)
        if ver is None or over is None or any(a is None for a in assets) or _WILD.search(spec):
            self.pkg_bad = True
            return
        for raw in _split(spec):
            self.pkgs.append((_unesc(raw), "" if ver is _ABSENT else ver, "" if over is _ABSENT else over,
                              "|".join("" if a is _ABSENT else a.lower() for a in assets)))

    def own_pkgsig(self):
        """frozenset of (lower-cased id, version, asset metadata) of this project's own package references (a
        version from the item, its VersionOverride, else the PackageVersion read for the group), or None when one
        cannot be read (docstring 12)."""
        if self.pkg_bad:
            return None
        out = set()
        for pid, ver, over, assets in self.pkgs:
            v = over or ver or self.pkg_versions.get(pid.lower(), "")
            if v is None:
                return None
            out.add((pid.lower(), v, assets))
        return frozenset(out)

    def link_of(self, path):
        """A Compile path ("/"-separated, relative to the PROJECT whichever file wrote it) as a links entry, or None
        when it stays inside the project's directory; ".." when it leaves the tree."""
        if path.startswith(_ROOT):
            parts = path[len(_ROOT):].split("/")
        elif path.startswith("/") or re.match(r"[A-Za-z]:", path):
            return ""
        else:
            parts = self.dir.split("/") + path.split("/")
        cut = next((i for i, p in enumerate(parts) if _WILD.search(p)), None)
        got = self.f.inside(parts if cut is None else parts[:cut])
        if got is None:
            return ".."                       # out of the tree: a file DeepMap never sees (CsBinder.linked_in)
        if not self.dir or got == self.dir or got.startswith(f"{self.dir}/"):
            return None
        return got if cut is None else (f"{got}/" if got else "")

    def apply(self, ops, items):
        """Apply item ops to the running list of [ns | None, alias ("" | str | None), static (bool | None), certain]."""
        for op in ops:
            if op[0] == "inc":
                _k, ns, alias, static, cert, why = op
                if alias is None:
                    self.reasons.add("alias_unread")
                if static is None:
                    self.reasons.add("static_unread")
                if ns is None or alias is None or static is None:
                    cert = False
                if not cert:
                    self.reasons.add(why)
                    if static is not False:
                        self.reasons.add("static_unread")
                items.append([ns, alias, static, cert])
            elif op[0] == "rem":
                _k, names, cert = op
                low = None if names is None else {n.lower() for n in names}
                if names is not None and cert:
                    keep = []
                    for x in items:
                        if x[0] is not None and x[0] in names:
                            continue                  # removed: the same letters (every file system)
                        if x[0] is not None and x[0].lower() in low:
                            x[3] = False              # only case-insensitively: removed on Windows, not on Linux
                            self.reasons.add("case_only")
                        keep.append(x)
                    items[:] = keep
                    continue
                self.reasons.add("remove_unread")
                for x in items:
                    if low is None or x[0] is None or x[0].lower() in low:
                        x[3] = False
            elif op[0] == "glob":                     # a wildcard Remove: it may match any item its head begins
                self.reasons.add("remove_unread")
                for x in items:
                    if x[0] is None or x[0].lower().startswith(op[1]):
                        x[3] = False
            elif op[0] == "upd":
                self.reasons.add("update_unread")
                for x in items:
                    if op[1] is None or x[0] is None or x[0].lower() in op[1]:
                        x[3] = False
            elif op[0] == "taint":                    # an unread input here may remove any earlier item
                self.reasons.add(op[1])
                for x in items:
                    x[3] = False

    # ---------------------------------------------------------------- the result
    def prop_final(self, low):
        """A property's final value: a string, None (unreadable: set unreadably, or never set after an unread input
        that may have set it) or _ABSENT (never set: MSBuild's empty default)."""
        got = self.v.get(low, _ABSENT)
        return None if got is _ABSENT and self.tainted else got

    def framework(self):
        """What the project builds for: "fx" (every framework .NET Framework), "core" (none of them), or None (a
        framework unknown, unreadable or missing, a mix, or a TargetFrameworkIdentifier a file set: _tfm_kind)."""
        if self.prop_final("targetframeworkidentifier") is not _ABSENT:
            return None
        if "targetframework" in self.glob:             # multi-targeting: the file is compiled once per framework
            tfs = self.prop_final("targetframeworks")
            names = _items(tfs) if isinstance(tfs, str) else []
        else:
            tf = _unesc(self.prop_final("targetframework"))
            names = [tf] if isinstance(tf, str) and tf else []
        kinds = {_tfm_kind(n) for n in names}
        return next(iter(kinds)) if len(kinds) == 1 else None

    def settle_sdk_halves(self):
        """An <Import Sdk>: an SDK only when both its props and its targets are imported; else its lists only possible
        (Microsoft.NET.Sdk.CSharp.props comes with the props, GenerateGlobalUsings with the targets)."""
        for name, parts in self.halves.items():
            if {"sdk.props", "sdk.targets"} <= parts:
                self.add_sdk(name)
            else:
                self.reasons.add("import_unread")
                self.add_sdk(name, maybe=True)

    def early_project(self) -> bool:
        """Does the project write anything before its own <Import Project="Sdk.props" Sdk=...>? That part precedes
        Directory.Build.props and Directory.Packages.props in MSBuild's order, which this walk does not model."""
        wrote = False
        for el in self.f.doc(self.rel):
            t = _local(el.tag)
            if t == "Import" and _attr(el, "Sdk") is not None \
                    and str(_attr(el, "Project") or "").strip().lower() == "sdk.props":
                return wrote
            wrote = wrote or t in ("PropertyGroup", "ItemGroup", "Import", "ImportGroup", "Choose")
        return False

    def compile_reading(self, sdk):
        """(the Compile ops of docstring 11, whether any of it is unread). An SDK project: ("base", None, True) when
        the SDK's default glob is provably on (EnableDefaultItems and EnableDefaultCompileItems unset, empty or
        boolean-true; every SDK known), else None; then the default excludes a file sets, then every in-directory
        Include / Remove AFTER the default glob (the project's, Directory.Build.targets', a Target's), in order. An
        old-style project (no SDK: no default glob) starts from ("base", None, False) and reads every phase's."""
        if sdk:
            # one half of an SDK (Sdk.props without Sdk.targets: EnableDefaultItems is never defaulted; Sdk.targets
            # alone: no default glob), one under an unreadable condition, or an SDK this module does not know
            base = None if self.maybe_sdks or any(n not in SDK_USINGS for n in self.sdks) else True
            for flag in ("enabledefaultitems", "enabledefaultcompileitems"):
                v = _unesc(self.prop_final(flag))
                if v is None or not (v is _ABSENT or v == "" or _boolish(v) is True):
                    base = None                      # off, or unreadable: which files compile is not read here
        else:
            base = None if self.sdk_text_unread else False
        ops = [("base", None, base)]
        if sdk:
            for prop in sorted(_LATE_EMPTY):
                v = self.prop_final(prop)
                if v is None:
                    ops.append(("rem", None, False))
                elif isinstance(v, str):
                    ops.extend(("rem", self.compile_pattern(one.replace("\\", "/")), True) for one in _items(v))
        order = {"project": 1, "targets": 2, "late": 3} if sdk else {"props": 0, "project": 1, "targets": 2, "late": 3}
        ops += [(op, pat, cert) for _ph, op, pat, cert in sorted(
            (x for x in self.compile_ops if x[0] in order), key=lambda x: order[x[0]])]
        unread = base is None or any(pat is None or not cert for op, pat, cert in ops[1:])
        return tuple(ops), unread

    def evaluate(self, props, targets, packages=(), rsp=False) -> MsbUsings:
        self.walk(props, targets, items=False, packages=packages)
        tf, tfs = _unesc(self.prop_final("targetframework")), self.prop_final("targetframeworks")
        if (tf is _ABSENT or tf == "") and isinstance(tfs, str) and _items(tfs):
            self.v = {}
            self.glob = dict(_GLOBALS, targetframework=_items(tfs)[0])
            self.walk(props, targets, items=False, packages=packages)  # MSBuild's inner build for the first framework
        self.settle_sdk_halves()
        hooked_imp = self.early_project() or any(k in _DIR_BUILD_SWITCHES or _IMPORT_HOOK.match(k) for k in self.v)
        hooks = {"import_unread": hooked_imp, "rsp": rsp, "local_property": self.local_prop,
                 "sdk_import": self.sdk_import}
        hooked = sorted(k for k, on in hooks.items() if on)
        for why in hooked:
            self.taint("targets", why)                         # one more input may set anything...
        self.walk(props, targets, items=True)
        for why in hooked:
            self.taint("targets", why)                         # ...and remove any item
        names = {self.stem}
        for prop in ("assemblyname", "rootnamespace"):
            v = _unesc(self.prop_final(prop))
            if isinstance(v, str) and v.strip():
                names.add(v.strip())
        comp, comp_unread = self.compile_reading(bool(self.sdks or self.maybe_sdks))
        # compile_unread does not decide `complete` here: whether it costs the compilation's using set depends on
        # what the files it may drop say (CsBinder.msbuild folds that in: a maybe-compiled file with a `global using`)
        extra = {"names": frozenset(names), "compile": comp, "compile_unread": comp_unread,
                 "pkgsig": self.own_pkgsig()}
        if self.projref_bad:
            self.reasons.add("projref_unread")
        if not self.sdks and not self.maybe_sdks:
            # an old-style project: no GenerateGlobalUsings target, nothing added (complete unless an unreadable
            # <Import> may bring an SDK in)
            why = {"import_unread"} if self.sdk_text_unread or hooked else set()
            why |= self.reasons & {"projref_unread"} | set(hooked)
            return _result([], why, self.links)._replace(**extra)
        iu = _unesc(self.prop_final("implicitusings"))
        implicit = None if iu is None else (isinstance(iu, str) and (iu.lower() == "enable" or _boolish(iu) is True))
        known = [s for s in self.sdks if s in SDK_USINGS]
        if any(s not in SDK_USINGS for s in self.sdks + self.maybe_sdks):
            self.reasons.add("sdk_unknown")
        sdk_ops: list = []
        if implicit is not False:
            lists = [(USINGS_BASE, bool(known), "sdk_unknown")] + [(SDK_USINGS[s], True, "") for s in known]
            lists += [(USINGS_BASE + SDK_USINGS[s], False, "import_unread") for s in self.maybe_sdks
                      if s in SDK_USINGS and s not in known]
            fw = self.framework()
            for lst, cert, why in lists:
                for ns in lst:
                    if ns == _NET_HTTP and fw == "fx":
                        continue
                    unknown = ns == _NET_HTTP and fw is None
                    sdk_ops.append(("inc", ns, "", False, cert and implicit is True and not unknown,
                                    "implicit_unread" if implicit is None else "framework_unread" if unknown else why))
        wpf = False
        for flag, word in (("usemaui", "maui"), ("usewpf", "windows_desktop"), ("usewindowsforms", "windows_desktop")):
            got = _unesc(self.prop_final(flag))
            if got is None or _boolish(got) is True:
                self.reasons.add(word)
                wpf = wpf or flag == "usewpf"
        items: list = []
        self.apply(self.phase_items["props"], items)
        self.apply(sdk_ops, items)
        self.apply(self.phase_items["project"], items)
        self.apply(self.phase_items["targets"], items)
        if self.target_usings:
            self.reasons.add("target_unread")
            late: list = []
            for u in self.target_usings:
                self.item(u, late, None, "late")               # run after evaluation: applied last, unreadable
            self.apply(late, items)
        if self.itemdef_using:
            self.reasons.add("itemdef_unread")
        if self.itemdef_using or wpf or not self.sdks:        # WPF's targets remove items too; with no whole SDK
            #                                                    there may be no GenerateGlobalUsings at all
            for x in items:
                x[3] = False
        return _result(items, self.reasons, self.links)._replace(**extra)


def _result(items, reasons, links=()) -> MsbUsings:
    certain: list = []
    possible, statics, alias_defs, reasons = set(), [], {}, set(reasons)
    for ns, alias, static, cert in items:
        if alias:
            alias_defs.setdefault(alias, []).append((ns, cert))
            continue
        if ns is None:
            continue
        if alias is None or static is None:
            possible.add(ns)                         # maybe a namespace using (an unreadable alias / Static)
            continue
        if static:
            if cert:
                if ns not in statics:
                    statics.append(ns)
            else:
                reasons.add("static_unread")
            continue
        if cert:
            if ns not in certain:
                certain.append(ns)
        else:
            possible.add(ns)
    aliases, unknown = {}, set()
    for name, defs in alias_defs.items():
        targets = {ns for ns, _c in defs}
        if len(targets) == 1 and None not in targets and all(c for _ns, c in defs):
            aliases[name] = next(iter(targets))
        else:
            unknown.add(name)
    cs = frozenset(certain)
    return MsbUsings(cs, frozenset(possible - cs), aliases, frozenset(unknown), tuple(statics), not reasons,
                     tuple(sorted(reasons)), links=frozenset(links))


class _Cond:
    """An MSBuild condition in three-valued logic: True, False or None (unreadable)."""

    def __init__(self, s, proj):
        self.s, self.p, self.i = s, proj, 0
        self.toks: list = []

    @staticmethod
    def _close(s, k):
        """The index of the ")" closing the "(" at s[k] (quotes skipped), or -1."""
        depth, q = 0, False
        for j in range(k, len(s)):
            ch = s[j]
            if ch == "'":
                q = not q
            elif not q and ch == "(":
                depth += 1
            elif not q and ch == ")":
                depth -= 1
                if depth == 0:
                    return j
        return -1

    def lex(self):
        s, i, out, n = self.s, 0, [], len(self.s)
        while i < n:
            ch = s[i]
            if ch.isspace():
                i += 1
            elif ch == "'":
                j = s.find("'", i + 1)
                if j < 0:
                    return None
                out.append(("str", s[i + 1:j]))
                i = j + 1
            elif s.startswith("==", i) or s.startswith("!=", i):
                out.append(("op", s[i:i + 2]))
                i += 2
            elif ch in "<>":
                out.append(("bad", ch))
                i += 2 if s.startswith("=", i + 1) else 1
            elif ch in "()!":
                out.append((ch, ch))
                i += 1
            elif ch in "$@%" and s.startswith("(", i + 1):
                j = self._close(s, i + 1)
                if j < 0:
                    return None
                out.append(("str" if ch == "$" else "unk", s[i:j + 1]))
                i = j + 1
            else:
                m = re.match(r"[A-Za-z0-9_.\-]+", s[i:])
                if m is None:
                    return None
                w = m.group(0)
                k = i + len(w)
                while k < n and s[k].isspace():
                    k += 1
                if k < n and s[k] == "(" and w.lower() not in ("and", "or"):
                    j = self._close(s, k)
                    if j < 0:
                        return None
                    out.append(("unk", s[i:j + 1]))        # Exists(...), HasTrailingSlash(...): unreadable
                    i = j + 1
                    continue
                out.append((w.lower(), w) if w.lower() in ("and", "or") else ("word", w))
                i += len(w)
        return out

    def run(self):
        try:
            toks = self.lex()
            if toks is None:
                return None
            self.toks, self.i = toks, 0
            v = self.or_()
            return None if v == "fail" or self.i != len(self.toks) else v
        except (IndexError, ValueError, RecursionError):
            return None

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def or_(self):
        v = self.and_()
        while self.peek()[0] == "or":
            self.i += 1
            r = self.and_()
            if v == "fail" or r == "fail":
                return "fail"
            v = True if (v is True or r is True) else (False if (v is False and r is False) else None)
        return v

    def and_(self):
        v = self.not_()
        while self.peek()[0] == "and":
            self.i += 1
            r = self.not_()
            if v == "fail" or r == "fail":
                return "fail"
            v = _and(v, r)
        return v

    def not_(self):
        if self.peek()[0] == "!":
            self.i += 1
            v = self.not_()
            return v if v == "fail" or v is None else (not v)
        return self.cmp()

    def value(self, tok):
        kind, text = tok
        if kind == "str":
            got = self.p.expand(text)
            return None if got is None or "\x00" in got else got
        return text if kind == "word" else None

    def cmp(self):
        tok = self.peek()
        if tok[0] == "(":
            self.i += 1
            v = self.or_()
            if self.peek()[0] != ")":
                return "fail"
            self.i += 1
            return v
        if tok[0] not in _OPERAND:
            return "fail"
        self.i += 1
        a = self.value(tok)
        nxt = self.peek()
        if nxt[0] == "bad":                           # <, >, <=, >=: a numeric / version comparison
            self.i += 1
            if self.peek()[0] not in _OPERAND:
                return "fail"
            self.i += 1
            return None
        if nxt[0] != "op":
            return _boolish(a)                        # a lone operand: boolean-like or unreadable
        self.i += 1
        btok = self.peek()
        if btok[0] not in _OPERAND:
            return "fail"
        self.i += 1
        b = self.value(btok)
        if a is None or b is None:
            return None
        ba, bb = _boolish(a), _boolish(b)
        if ba is not None and bb is not None:         # R34: two boolean-like operands compare as booleans
            return (ba == bb) if nxt[1] == "==" else (ba != bb)
        eq = a.lower() == b.lower()                   # no trim: `' a ' == 'a'` is false in MSBuild
        if not eq and _NUM.match(a.strip()) and _NUM.match(b.strip()):
            return None                               # MSBuild compares two numbers numerically
        return eq if nxt[1] == "==" else not eq


# ================================================================================================
# The entry point
# ================================================================================================

def _nearest(files, start_dir, lname):
    """(the root-relative path of the nearest file `lname` in start_dir or an ancestor inside root, or None; whether
    one exists only above root)."""
    cur = start_dir
    for _ in range(_MAX_UP):
        hit = files.find(cur, lname)
        if hit is not None:
            return hit, False
        if not cur:
            break
        cur = posixpath.dirname(cur)
    return None, files.above_root(_SPELLED[lname])


def _place(hit, above, lname) -> list:
    """What MSBuild imports at that place: [the file], [_CASE] when its name has another letter case than MSBuild's
    own spelling (a case-sensitive file system does not import it, R39), [_ABOVE] (only above root) or []."""
    if hit:
        return [hit if posixpath.basename(hit) == _SPELLED[lname] else _CASE]
    return [_ABOVE] if above else []


def _one(files, rel, touched=None) -> tuple:
    """(project rel's MsbUsings without its ProjectReference closure, its ProjectReference paths)."""
    p = _Proj(files, rel, touched)
    props, above_p = _nearest(files, p.dir, _PROPS)
    targets, above_t = _nearest(files, p.dir, _TARGETS)
    pkgs, above_k = _nearest(files, p.dir, _PACKAGES)
    rsp, above_r = _nearest(files, p.dir, _RSP)
    got = p.evaluate(_place(props, above_p, _PROPS), _place(targets, above_t, _TARGETS),
                     _place(pkgs, above_k, _PACKAGES), rsp=bool(rsp) or bool(above_r))
    return got, tuple(p.projrefs)


def _eval(files, rel):
    """(MsbUsings, ProjectReference paths, the files read) of project rel, memoised on the shared tree; a _Bad when
    it cannot be read."""
    if rel not in files.evals:
        touched: set = set()
        try:
            got, refs = _one(files, rel, touched)
            files.evals[rel] = (got, refs, frozenset(touched))
        except _Bad as exc:
            files.evals[rel] = exc
    return files.evals[rel]


def _exact(files, rel):
    """rel when the tree holds a file of exactly that spelling, else None (a case-only match is no match on Linux)."""
    d, n = posixpath.split(rel)
    names = files.listdir(d)
    return rel if names and n in names else None


def _closure(files, rel, own, refs) -> tuple:
    """(the package set over project rel and its ProjectReference closure, every reference read?): R37. None when a
    project of the closure has a package this module cannot read, or a reference cannot be read."""
    sig, whole = own.pkgsig, True
    seen, todo = {rel}, list(refs)
    while todo:
        r = _exact(files, todo.pop())
        if r is None:
            whole = False
            continue
        if r in seen:
            continue
        seen.add(r)
        got = _eval(files, r)
        if isinstance(got, _Bad):
            whole = False
            continue
        m, more, _t = got
        if "projref_unread" in m.reasons:
            whole = False
        sig = None if sig is None or m.pkgsig is None else sig | m.pkgsig
        todo.extend(more)
    return (sig if whole else None), whole


def _merge(rs) -> MsbUsings:
    certain = frozenset.intersection(*[r.certain for r in rs])
    union = frozenset.union(*[r.certain for r in rs])
    possible = (union - certain).union(*[r.possible for r in rs]) - certain
    unknown = set().union(*[set(r.alias_unknown) for r in rs])
    aliases = {}
    for n in set().union(*[set(r.aliases) for r in rs]):
        vals = {r.aliases.get(n) for r in rs}
        if len(vals) == 1 and None not in vals and n not in unknown:
            aliases[n] = next(iter(vals))
        else:
            unknown.add(n)
    reasons = set().union(*[set(r.reasons) for r in rs]) | {"two_projects"}
    statics = tuple(s for s in rs[0].statics if all(s in r.statics for r in rs))
    if any(set(r.statics) != set(statics) for r in rs):
        reasons.add("static_unread")
    same_comp = all(r.compile == rs[0].compile for r in rs)
    sigs = {r.pkgsig for r in rs}
    return MsbUsings(certain, possible, aliases, frozenset(unknown), statics, False, tuple(sorted(reasons)),
                     links=frozenset().union(*[r.links for r in rs]), names=frozenset().union(*[r.names for r in rs]),
                     compile=rs[0].compile if same_comp else (("base", None, None),),
                     compile_unread=any(r.compile_unread for r in rs) or not same_comp,
                     pkgsig=next(iter(sigs)) if len(sigs) == 1 else None)


def _unread(why) -> MsbUsings:
    return MsbUsings(frozenset(), frozenset(), {}, frozenset(), (), False, (why,), compile=(("base", None, None),),
                     compile_unread=True, pkgsig=None)


def _glob_rx(pattern, flags):
    """A root-relative Compile glob (`**` any directories, `*` / `?` inside one segment) as a regex."""
    parts, out = pattern.split("/"), []
    for i, s in enumerate(parts):
        last = i == len(parts) - 1
        if s == "**":
            out.append("(?:[^/]+/)*[^/]+" if last else "(?:[^/]+/)*")
            continue
        out.append("".join("[^/]*" if ch == "*" else "[^/]" if ch == "?" else re.escape(ch) for ch in s))
        if not last:
            out.append("/")
    return re.compile("".join(out) + r"\Z", flags)


def compile_state(msb, group_dir, rel):
    """R25: does the project of group_dir compile .cs file rel (root-relative, under group_dir), as reading msb
    (MsbUsings.compile) says? True; False (a certain, readable Compile Remove / default exclude takes it out, or an
    old-style project lists it nowhere); None (maybe: an unreadable or conditional item, the default glob not provably
    on, a match only in another letter case, an unread input that may add or remove items). A file outside group_dir
    (no file of that project's default glob): True. Never raises (an error: None)."""
    try:
        g = str(group_dir or "").replace("\\", "/").strip("/")
        r = str(rel).replace("\\", "/")
        if g and not r.startswith(g + "/"):
            return True
        state = True
        for op, pat, cert in tuple(getattr(msb, "compile", ()) or ()):
            if op == "base":
                state = cert
                continue
            if op == "any" or pat is None:
                hit = None
            elif _glob_rx(pat, 0).match(r):
                hit = True
            elif _glob_rx(pat, re.I).match(r):
                hit = None                           # only case-insensitively: Windows matches, Linux does not
            else:
                continue
            sure = hit is True and cert is True
            if op == "rem":
                state = False if sure or state is False else None
            elif op == "inc":
                state = True if sure or state is True else None
            else:
                state = None
        return state
    except Exception:  # noqa: BLE001
        return None


def tree(root, *, read=None, listdir=None):
    """The file access read_group works through (same arguments), to share between the groups of one tree (files=): a
    Directory.Build.props is then parsed once and above_root walks once. It caches, so it lives as long as one pass."""
    return _Files(root, read, listdir)


def read_group(root, group_dir, *, read=None, listdir=None, files=None) -> MsbUsings:
    """What MSBuild adds as `global using` directives to the compilation of the .csproj(s) directly in group_dir
    (root-relative, "/"-separated; "" = root): see the module docstring. `read`: None (the filesystem under root), a
    callable read(rel) -> bytes | None, or a Mapping {rel: bytes | str} (an in-memory tree: a key "../name" is a file
    above root). `listdir(rel_dir) -> file names | None` goes with a callable read (default: the filesystem). `files`:
    a tree(...) shared across groups (root, read, listdir then unused). Never raises."""
    try:
        files = files if files is not None else _Files(root, read, listdir)
        g = str(group_dir or "").replace("\\", "/").strip("/")
        g = _norm(g.split("/")) if g else ""
        if g is None:
            return _unread("unreadable")
        names = files.listdir(g)
        if names is None:
            return _unread("unreadable")
        projs = sorted(n for n in names if n.lower().endswith(".csproj"))
        touched = {f"{g}/{n}" if g else n for n in names if n.lower() == _PKG_CONFIG_NAME}
        touched |= {f"{g}/{n}" if g else n for n in projs}
        if not projs:
            out = EMPTY
        else:
            rs, bad = [], None
            for n in projs:
                prel = f"{g}/{n}" if g else n
                got = _eval(files, prel)
                if isinstance(got, _Bad):
                    bad = got
                    break
                m, refs, read = got
                touched |= read
                sig, whole = _closure(files, prel, m, refs)
                if not whole:
                    m = m._replace(complete=False, reasons=tuple(sorted(set(m.reasons) | {"projref_unread"})))
                rs.append(m._replace(pkgsig=sig))
            if bad is not None:
                out = _unread(bad.why if bad.why in ("xml", "unreadable") else "xml")
            else:
                out = rs[0] if len(rs) == 1 else _merge(rs)
        ids = frozenset().union(*[files.package_ids(r) for r in sorted(touched)]) if touched else frozenset()
        return out._replace(packages=ids) if ids else out
    except Exception:  # noqa: BLE001 — never raises: an unreadable reading
        return _unread("error")
