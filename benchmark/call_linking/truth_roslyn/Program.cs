// C# call-site reference resolver for D3-cs (benchmark/call_linking/PREREG_CS.md §8.2, "The reference contract").
// MEASUREMENT ONLY: nothing under build/ or hooks/ uses it, Roslyn is in no shipped list
// (tests/call_linking_harness_gates.py A/G), and its output is a reference, not the truth (the blind audit decides).
//
//   dotnet bin/Release/net8.0/TruthRoslyn.dll <corpus> <out.jsonl> --tracked=<file> --file-list=git|listed
//        [--threads=N] [--record-sdk=VERSION] [--record-head=SHA] [--record-tool=SHA256]
//   benchmark/call_linking/truth_roslyn.py runs it (it writes the --tracked list with `git ls-files -z`) and turns
//   these raw rows into the kit's call-linking/truth/1 rows (defs and dispatch are derived there, gate-checkably).
//
// There is no option that changes what is emitted: the contract is the only mode. --threads changes nothing in the
// output; --record-* only stamp provenance into the header.
//
// THE CONTRACT, as implemented here (PREREG_CS §8.2, with its "Amendments before RC" AC1-AC4 where they govern):
//  * Compilations: one per git-tracked .csproj (the --tracked list is the git-tracked set), as MSBuild builds them (AC3):
//    - evaluation: the nearest Directory.Build.props above the project (in its own directory or the closest ancestor)
//      and whatever it <Import>s, then the project, then the nearest Directory.Build.targets and its imports, exactly
//      MSBuild's lookup (a props file one level up is read only when the nearer one imports it, e.g. with
//      $([MSBuild]::GetPathOfFileAbove('Directory.Build.props', '$(MSBuildThisFileDirectory)../')) or
//      $([MSBuild]::GetDirectoryNameOfFileAbove(...))/Directory.Build.props, both evaluated here); the last assignment
//      of a property wins.
//    - files: every tracked .cs under the project's directory that no deeper .csproj claims, minus the project's own
//      bin/ and obj/ (MSBuild's default excludes, relative to the project directory) and its <Compile Remove> items,
//      plus <Compile Include> items (links). Items are applied in MSBuild's order: Directory.Build.props items, the
//      SDK's default glob, the project's items, Directory.Build.targets items. UseMaui=true (the MAUI single-project
//      layout): a .cs under the project's Platforms/ folder is compiled only under Platforms/<the folder of the first
//      target framework's platform>/ (Android, iOS, MacCatalyst, Windows, Tizen), as the MAUI SDK's own
//      _MauiRemovePlatformCompileItems Compile Remove does (it runs after evaluation, so it is applied last); the
//      others are "uncompiled_files" with reason maui_platform.
//    - references: the reference assemblies of Microsoft.NETCore.App.Ref 8.0.3 and Microsoft.AspNetCore.App.Ref 8.0.3
//      (packs/ of the dotnet that runs the tool), then the compilations of the project's <ProjectReference>s: the
//      transitive closure (MSBuild's default; DisableTransitiveProjectReferences=true keeps the direct ones only), in
//      dependency order with ties broken by byte order of the paths (every project in the lexicographically smallest
//      topological order of the reference graph: at each step the byte-smallest project whose references are all
//      placed), skipping OutputItemType="Analyzer", ReferenceOutputAssembly="false" and, under Aspire.AppHost.Sdk, a
//      reference without IsAspireProjectResource="false" (a resource, not an assembly reference). No NuGet package
//      assembly, no <Reference>/HintPath assembly.
//    - implicit usings: the SDK's list (SDK 8.0.202's props files: Microsoft.NET.Sdk's seven namespaces; .Web, .Worker
//      and .BlazorWebAssembly add theirs; .Razor adds none; any other SDK, e.g. Aspire.AppHost.Sdk or MSTest.Sdk, takes
//      Microsoft.NET.Sdk's list) when ImplicitUsings is enable/true after the evaluation above (the project and the
//      Directory.Build.props files MSBuild imports for it; the last assignment wins); <Using Include> items are added
//      (with Alias / Static) and <Using Remove> items removed, as the SDK's GenerateGlobalUsings target writes them.
//    - generated assembly attributes (AC3): the InternalsVisibleTo attributes MSBuild writes (the SDK's
//      Microsoft.NET.GenerateAssemblyInfo.targets): every <AssemblyAttribute Include="System.Runtime.CompilerServices.
//      InternalsVisibleTo[Attribute]"> item's _Parameter1, and every <InternalsVisibleTo Include> item ("Name", or
//      "Name, PublicKey=..." from its Key / PublicKey metadata or $(PublicKey)) unless
//      GenerateInternalsVisibleToAttributes is set to anything but true; none at all unless GenerateAssemblyInfo is
//      empty or true. No other generated attribute (they change no binding).
//    - parse options: LanguageVersion.CSharp12; preprocessor symbols TRACE, RELEASE, the target-framework symbols of
//      the project's first target framework (as an SDK that supports it defines them: NET, NETn_m, NETCOREAPP, the
//      NETCOREAPPx_y_OR_GREATER and NETk_0_OR_GREATER series ...), the platform symbol of a platform-suffixed
//      framework (ANDROID, IOS, WINDOWS ...), plus the tokens of <DefineConstants>.
//    - assembly name = <AssemblyName> (else the project file name), so InternalsVisibleTo attributes apply; output kind
//      from <OutputType> (Web / Worker SDKs default to Exe); <Nullable> as declared. No analyzer and no source
//      generator runs.
//    - Property and item conditions are evaluated with Configuration=Release, Platform=AnyCPU and TargetFramework = the
//      first target framework (global properties); a condition this evaluator cannot read (property functions, item
//      lists) counts as false and is listed in the project's "notes".
//    - a git-tracked .cs that no project compiles and that is in no project's tree is compiled once, in a leftover
//      compilation (assembly TruthRoslyn.Leftover, LanguageVersion.CSharp12, no preprocessor symbols, no implicit
//      usings) that references every project. A tracked .cs inside a project's tree that the project does not compile
//      (bin/obj, <Compile Remove>, a MAUI platform folder) is listed in "uncompiled_files".
//    - a file held by several compilations takes its rows from one: its own directory's project (the project whose
//      tree claims it) when that project compiles it, else the first of the others in byte order of their paths.
//  * Sites: every InvocationExpressionSyntax (nameof(...) excluded) and every object creation (`new T(...)`,
//    `new T { }`, target-typed `new(...)`) in every compiled file, files with syntax errors included.
//  * form (from syntax): bare `M()` / `M<T>()`; this `this.M()`; super `base.M()`; name_attr `x.M()` with x an
//    IdentifierName, a GenericName or a predefined type keyword (nothing unwrapped: `(x).M()` and `x!.M()` are recv);
//    recv any other receiver; `x?.M()` takes the form `x.M()` would, a longer conditional chain is recv; ctor an object
//    creation; other `(f)()`, `p->M()`, `a[i]()` ...
//  * name: the invoked member's identifier (no type arguments); for an object creation the created type's simple
//    name (the written rightmost identifier when no type is bound).
//  * defstack: [[qualname, line, alt lines]], outermost first: the enclosing method / constructor / destructor /
//    operator / conversion operator / property / indexer / event declaration, then the enclosing local functions
//    (lambdas are never entries). qualname = containing type chain (no namespace) + member name (a constructor named
//    after its type, a destructor ~T, an indexer this, an operator `operator +`); line = the member's first line
//    counting its attribute lists; alt = the first line after its attribute lists, its name line, for an overloaded
//    method or constructor the first line of the first same-named member of the same type declaration and, for a
//    partial method's implementation part, the first line of its defining declaration when that is in the same file
//    (AC4: graphify's only node is there; the alt lines are one rule for defstack entries and defs).
//  * target (the STATIC binding): GetSymbolInfo(site).Symbol only (a site with only candidate symbols is
//    unresolved: n_candidates). Taken as its original definition: ReducedFrom for an extension call, OriginalDefinition
//    for a generic or constructed member, PartialImplementationPart when a partial method has one. An object creation
//    binds to the created TYPE (every declaration of a partial type). decls = every declaration of it in a compiled
//    corpus file (none: external, implicitly declared, or a delegate's Invoke), with the alt lines above.
//  * dispatch_raw (the runtime targets beyond the static binding, BEFORE the adapter drops bodiless ones and, for a
//    caller outside test files, test-file ones):
//    - S a non-static member of an interface: for every non-interface type T declared in corpus source (every
//      compilation) that implements S's interface and either is the receiver's static type, derives from it or
//      implements it (for a type-parameter receiver: every T implementing S's interface), the method an object of
//      exact type T runs (AC1): T.FindImplementationForInterfaceMember for S as a member of T's own constructed view
//      of S's interface (the view(s) the receiver reaches; every view for a type-parameter receiver) and, when that is
//      a virtual, abstract or override class member, the most-derived override of it on T's base chain (an override
//      whose OverriddenMethod chain reaches it; a `new` slot is not one), when it is declared in corpus source and is
//      not S. A bodiless one (T abstract) is dropped by the adapter.
//    - S a virtual, abstract or override (not sealed) member of a class or struct, called through any receiver but
//      `base.`: every override declared in corpus source whose OverriddenMethod chain reaches S's root declaration,
//      on a type that is the receiver's static type or derives from it (this. and an implicit receiver: the
//      containing type), other than S. AC2: an interface receiver (calling an object member) has none; a
//      type-parameter receiver is read as its class constraint (the most derived class constraint of it or of a
//      type parameter it is constrained to: its effective base class), else System.Object, or System.ValueType under
//      a struct / unmanaged constraint (raw receiver_reading says which).
//    - otherwise none (dispatch_none says why): static, non-virtual, sealed, base. call, extension, local function,
//      delegate Invoke, object creation.
//    Types are compared across compilations by (assembly name, documentation-comment id), with type parameters on
//    either side matching anything, so a compilation reference that Roslyn retargets cannot hide a target. One result
//    is cached per (rule, S, the receiver as the rule reads it, the interface S was reached through): the override
//    rule keys a type-parameter receiver on its AC2 reading, so no result depends on the order files reach the cache.
//  * dispatch_ac1_added: a DIAGNOSTIC, never part of the truth rows: the interface-rule targets AC1 adds beyond the
//    members FindImplementationForInterfaceMember returns (the registered text before AC1). Counted in the header
//    (ac1_rows / ac1_targets) so the amendment's effect is sized.
//  * internals_visible_to: "generated" when the site binds a member that is internal (or private protected, or
//    protected internal outside a derived type), itself or through a containing type, of ANOTHER corpus project's
//    assembly that an MSBuild-generated InternalsVisibleTo attribute (AC3) opens to the caller's assembly; "source"
//    when only a source attribute can open it; else null. Counted in the header (ivt_sites:*).
//  * lines are counted on '\n' only (tree-sitter's rows); columns are 0-based UTF-16 offsets.
//  * determinism: rows sorted (file, line, col, name, name_line, name_col, site kind), every list sorted, no timing,
//    no absolute path in the output: two runs are byte-identical, for any --threads.
//
// Output: JSON lines, UTF-8, LF. Line 1 = the header (schema call-linking/roslyn-sites/2: resolver, packs, every
// project's evaluation, the leftover compilation, files, parse errors, counts); then one row per site.
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Xml.Linq;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;
using Microsoft.CodeAnalysis.Operations;
using Microsoft.CodeAnalysis.Text;

namespace TruthRoslyn;

internal sealed class Options
{
    public string Root = "";
    public string Out = "";
    public string Tracked = "";
    public string FileList = "";
    public string? Sdk;
    public string? Head;
    public string? Tool;
    public int Threads = Environment.ProcessorCount;
}

/// <summary>Line numbers counted on '\n' only (tree-sitter's rows; Roslyn's own line table also breaks on '\r',
/// U+0085, U+2028 and U+2029).</summary>
internal sealed class LfLines
{
    private readonly int[] _starts;

    public LfLines(SourceText text)
    {
        var starts = new List<int> { 0 };
        string s = text.ToString();
        for (int i = 0; i < s.Length; i++)
        {
            if (s[i] == '\n')
            {
                starts.Add(i + 1);
            }
        }
        _starts = starts.ToArray();
    }

    public int Line(int pos)
    {
        int i = Array.BinarySearch(_starts, pos);
        if (i < 0)
        {
            i = ~i - 1;
        }
        return i + 1;
    }

    public int Col(int pos) => pos - _starts[Line(pos) - 1];
}

// ======================================================================================== corpus-relative paths
internal static class P
{
    /// <summary>The corpus root inside expanded MSBuild values ($(MSBuildThisFileDirectory) ...): never written out.</summary>
    public const string Root = "/@root";

    public static string Dir(string rel)
    {
        int i = rel.LastIndexOf('/');
        return i < 0 ? "" : rel[..i];
    }

    public static string Name(string rel)
    {
        int i = rel.LastIndexOf('/');
        return i < 0 ? rel : rel[(i + 1)..];
    }

    public static bool Under(string rel, string dir) =>
        dir.Length == 0 || rel.StartsWith(dir + "/", StringComparison.Ordinal);

    public static string AsMsBuildDir(string dir) => dir.Length == 0 ? Root : Root + "/" + dir;

    /// <summary>An MSBuild path or glob (relative to `baseDir`, or rooted by Root) as a normalized corpus-relative
    /// path; null when it is absolute or leaves the corpus.</summary>
    public static string? Resolve(string spec, string baseDir)
    {
        string s = spec.Trim().Replace('\\', '/');
        if (s.Length == 0)
        {
            return null;
        }
        var parts = new List<string>();
        if (s == Root || s.StartsWith(Root + "/", StringComparison.Ordinal))
        {
            s = s[Root.Length..];
        }
        else if (s.StartsWith('/') || (s.Length > 1 && s[1] == ':'))
        {
            return null;
        }
        else if (baseDir.Length > 0)
        {
            parts.AddRange(baseDir.Split('/'));
        }
        foreach (var seg in s.Split('/'))
        {
            if (seg.Length == 0 || seg == ".")
            {
                continue;
            }
            if (seg == "..")
            {
                if (parts.Count == 0)
                {
                    return null;
                }
                parts.RemoveAt(parts.Count - 1);
                continue;
            }
            parts.Add(seg);
        }
        return string.Join("/", parts);
    }

    public static bool IsGlob(string s) => s.IndexOf('*') >= 0 || s.IndexOf('?') >= 0;

    /// <summary>MSBuild's glob: ** any number of directories, * within a segment, ? one character; case-insensitive
    /// (MSBuild on Windows).</summary>
    public static Regex Glob(string pattern)
    {
        var sb = new StringBuilder("^");
        var segs = pattern.Split('/');
        for (int i = 0; i < segs.Length; i++)
        {
            string seg = segs[i];
            bool last = i == segs.Length - 1;
            if (seg == "**")
            {
                sb.Append(last ? ".*" : "(?:[^/]+/)*");
                continue;
            }
            foreach (char ch in seg)
            {
                sb.Append(ch switch { '*' => "[^/]*", '?' => "[^/]", _ => Regex.Escape(ch.ToString()) });
            }
            if (!last)
            {
                sb.Append('/');
            }
        }
        sb.Append('$');
        return new Regex(sb.ToString(), RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
    }
}

internal sealed class Corpus
{
    public required string Root;                                     // absolute; never written to the output
    public required List<string> Tracked;                            // ordinal
    public required HashSet<string> TrackedSet;
    public required Dictionary<string, string> TrackedCi;            // lower-invariant -> rel
    public required List<string> Cs;                                 // tracked *.cs, ordinal
    private readonly Dictionary<string, XDocument?> _docs = new(StringComparer.Ordinal);
    public readonly SortedDictionary<string, string> XmlErrors = new(StringComparer.Ordinal);

    public string? Find(string rel) =>
        TrackedSet.Contains(rel) ? rel : TrackedCi.GetValueOrDefault(rel.ToLowerInvariant());

    public XDocument? Load(string rel)
    {
        lock (_docs)
        {
            if (_docs.TryGetValue(rel, out var d))
            {
                return d;
            }
            try
            {
                d = XDocument.Load(Path.Combine(Root, rel), LoadOptions.None);
            }
            catch (Exception exc)
            {
                XmlErrors[rel] = exc.GetType().Name;
                d = null;
            }
            _docs[rel] = d;
            return d;
        }
    }

    /// <summary>The tracked `name` (case-insensitive) in `dir` itself, else in its closest ancestor up to the corpus
    /// root; null when there is none. MSBuild's Directory.Build.props / .targets lookup (GetDirectoryNameOfFileAbove
    /// from the project directory) and the GetPathOfFileAbove / GetDirectoryNameOfFileAbove property functions.</summary>
    public string? Nearest(string dir, string name)
    {
        for (string d = dir; ; d = P.Dir(d))
        {
            string? f = Find(d.Length == 0 ? name : d + "/" + name);
            if (f != null)
            {
                return f;
            }
            if (d.Length == 0)
            {
                return null;
            }
        }
    }
}

// ============================================================================== MSBuild-lite project evaluation
internal sealed record ItemOp(string Phase, string Type, string Op, string Spec, string? Exclude,
                              IReadOnlyDictionary<string, string> Meta, string From);

internal sealed class ProjectInfo
{
    public required string Rel;
    public required string Dir;
    public required string Name;
    public string AssemblyName = "";
    public readonly List<string> Sdks = new();
    public string? FirstTf;
    public readonly SortedSet<string> Defines = new(StringComparer.Ordinal);
    public bool ImplicitUsings;
    public readonly List<string> UsingLines = new();
    public string OutputKind = "DynamicallyLinkedLibrary";
    public string Nullable = "Disable";
    public bool DefaultCompile = true;
    public bool TransitiveRefs = true;
    public bool GenerateAssemblyInfo = true;
    public bool GenerateIvtAttributes = true;
    public string PublicKey = "";
    public bool UseMaui;
    public readonly List<string> Files = new();
    public readonly List<string> Links = new();
    public readonly List<string> Removed = new();
    public readonly List<string> MauiRemoved = new();
    public readonly List<string> Ivt = new();              // the generated InternalsVisibleTo arguments, ordinal
    public readonly List<string> DirectRefs = new();
    public readonly List<JsonNode> SkippedRefs = new();
    public readonly List<string> Closure = new();
    public readonly SortedSet<string> Notes = new(StringComparer.Ordinal);
    public readonly List<string> Read = new();
    public readonly List<ItemOp> Items = new();
}

internal sealed class MsEval
{
    private readonly Corpus _c;
    private readonly ProjectInfo _p;
    private readonly Dictionary<string, string> _v = new(StringComparer.OrdinalIgnoreCase);
    private readonly HashSet<string> _global = new(StringComparer.OrdinalIgnoreCase);
    private string _thisFile = "";
    private readonly List<string> _stack = new();          // the files being read (an Import of one of them: a cycle)
    private string _phase = "";
    private bool _items;
    private bool _record;

    private MsEval(Corpus c, ProjectInfo p, string? tf)
    {
        _c = c;
        _p = p;
        Set("Configuration", "Release", global: true);
        Set("Platform", "AnyCPU", global: true);
        if (tf != null)
        {
            Set("TargetFramework", tf, global: true);
        }
        _v["MSBuildProjectName"] = p.Name;
        _v["MSBuildProjectFile"] = P.Name(p.Rel);
        _v["MSBuildProjectExtension"] = ".csproj";
        _v["MSBuildProjectDirectory"] = P.AsMsBuildDir(p.Dir);
        _v["MSBuildProjectFullPath"] = P.AsMsBuildDir(p.Dir) + "/" + P.Name(p.Rel);
        _v["Language"] = "C#";
        _v["DefaultLanguageSourceExtension"] = ".cs";
    }

    private void Set(string k, string v, bool global)
    {
        _v[k] = v;
        if (global)
        {
            _global.Add(k);
        }
    }

    private string Get(string k) => _v.GetValueOrDefault(k) ?? "";

    public static void Evaluate(Corpus c, ProjectInfo p)
    {
        // MSBuild imports the NEAREST Directory.Build.props / .targets only (Microsoft.Common.props / .targets:
        // GetDirectoryNameOfFileAbove($(MSBuildProjectDirectory), ...)); a farther one is read only through an Import
        var props = c.Nearest(p.Dir, "Directory.Build.props") is { } np ? new List<string> { np } : new List<string>();
        var targets = c.Nearest(p.Dir, "Directory.Build.targets") is { } nt ? new List<string> { nt } : new List<string>();
        // pass A: the first target framework (TargetFramework, else the first of TargetFrameworks)
        var a = new MsEval(c, p, null);
        a.Walk(props, targets);
        string? tf = FirstTf(a.Get("TargetFramework"), a.Get("TargetFrameworks"));
        // pass B: properties with that framework as a global property (MSBuild's inner build); then the items, every
        // condition read with the final property values (MSBuild evaluates properties before items)
        var b = new MsEval(c, p, tf) { _record = true };
        b.Walk(props, targets);
        b._items = true;
        b._record = false;
        b.Walk(props, targets);
        p.FirstTf = tf;
        string asm = b.Get("AssemblyName").Trim();
        p.AssemblyName = asm.Length > 0 ? asm : p.Name;
        p.ImplicitUsings = b.Get("ImplicitUsings").Trim() is var iu &&
                           (iu.Equals("enable", StringComparison.OrdinalIgnoreCase) ||
                            iu.Equals("true", StringComparison.OrdinalIgnoreCase));
        p.Defines.Add("TRACE");
        p.Defines.Add("RELEASE");
        foreach (var s in TfSymbols(tf))
        {
            p.Defines.Add(s);
        }
        foreach (var tok in b.Get("DefineConstants").Split(';', StringSplitOptions.RemoveEmptyEntries |
                                                                StringSplitOptions.TrimEntries))
        {
            if (Regex.IsMatch(tok, @"^[A-Za-z_][A-Za-z0-9_]*$"))
            {
                p.Defines.Add(tok);
            }
        }
        string ot = b.Get("OutputType").Trim().ToLowerInvariant();
        if (ot.Length == 0 && p.Sdks.Any(s => s.Equals("Microsoft.NET.Sdk.Web", StringComparison.OrdinalIgnoreCase) ||
                                               s.Equals("Microsoft.NET.Sdk.Worker", StringComparison.OrdinalIgnoreCase)))
        {
            ot = "exe";
        }
        p.OutputKind = ot switch
        {
            "exe" => "ConsoleApplication",
            "winexe" => "WindowsApplication",
            _ => "DynamicallyLinkedLibrary",
        };
        p.Nullable = b.Get("Nullable").Trim().ToLowerInvariant() switch
        {
            "enable" => "Enable",
            "warnings" => "Warnings",
            "annotations" => "Annotations",
            _ => "Disable",
        };
        p.DefaultCompile = !b.Get("EnableDefaultCompileItems").Trim().Equals("false", StringComparison.OrdinalIgnoreCase) &&
                           !b.Get("EnableDefaultItems").Trim().Equals("false", StringComparison.OrdinalIgnoreCase);
        p.TransitiveRefs = !b.Get("DisableTransitiveProjectReferences").Trim().Equals("true",
                                                                                     StringComparison.OrdinalIgnoreCase);
        // Microsoft.NET.GenerateAssemblyInfo.targets: each switch defaults to true when empty, and acts when 'true'
        p.GenerateAssemblyInfo = EmptyOrTrue(b.Get("GenerateAssemblyInfo"));
        p.GenerateIvtAttributes = EmptyOrTrue(b.Get("GenerateInternalsVisibleToAttributes"));
        p.PublicKey = b.Get("PublicKey").Trim();
        p.UseMaui = b.Get("UseMaui").Trim().Equals("true", StringComparison.OrdinalIgnoreCase);
    }

    private static bool EmptyOrTrue(string v) =>
        v.Trim().Length == 0 || v.Trim().Equals("true", StringComparison.OrdinalIgnoreCase);

    /// <summary>MSBuild's GetTargetPlatformIdentifier, lower-cased: "ios" for net10.0-ios, "windows" for
    /// net10.0-windows10.0.19041.0, "" for a framework without a platform.</summary>
    public static string TargetPlatform(string? tf)
    {
        string t = (tf ?? "").Trim().ToLowerInvariant();
        int dash = t.IndexOf('-');
        return dash < 0 ? "" : Regex.Match(t[(dash + 1)..], "^[a-z]+").Value;
    }

    private static string? FirstTf(string tf, string tfs)
    {
        if (tf.Trim().Length > 0)
        {
            return tf.Trim();
        }
        return tfs.Split(';', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).FirstOrDefault();
    }

    /// <summary>The preprocessor symbols MSBuild defines for a target framework (the SDK's
    /// Microsoft.NET.Sdk.BeforeCommon.targets rules, for an SDK that supports that framework), plus the platform
    /// symbol of a platform-suffixed one.</summary>
    public static List<string> TfSymbols(string? tf)
    {
        var o = new List<string>();
        if (string.IsNullOrWhiteSpace(tf))
        {
            return o;
        }
        string t = tf.Trim().ToLowerInvariant();
        string core = t, plat = "";
        int dash = t.IndexOf('-');
        if (dash >= 0)
        {
            core = t[..dash];
            plat = t[(dash + 1)..];
        }
        string[] coreApp = { "1.0", "1.1", "2.0", "2.1", "2.2", "3.0", "3.1" };
        Match m;
        if ((m = Regex.Match(core, @"^net(\d+)\.(\d+)$")).Success && int.Parse(m.Groups[1].Value, CultureInfo.InvariantCulture) >= 5)
        {
            int maj = int.Parse(m.Groups[1].Value, CultureInfo.InvariantCulture);
            int min = int.Parse(m.Groups[2].Value, CultureInfo.InvariantCulture);
            o.Add("NET");
            o.Add($"NET{maj}_{min}");
            o.Add("NETCOREAPP");
            o.AddRange(coreApp.Select(v => $"NETCOREAPP{v.Replace('.', '_')}_OR_GREATER"));
            for (int k = 5; k <= maj; k++)
            {
                o.Add($"NET{k}_0_OR_GREATER");
            }
            var pm = Regex.Match(plat, "^[a-z]+");
            if (pm.Success)
            {
                o.Add(pm.Value.ToUpperInvariant());
            }
        }
        else if ((m = Regex.Match(core, @"^netcoreapp(\d+)\.(\d+)$")).Success)
        {
            var v = new Version(int.Parse(m.Groups[1].Value, CultureInfo.InvariantCulture),
                                int.Parse(m.Groups[2].Value, CultureInfo.InvariantCulture));
            o.Add("NETCOREAPP");
            o.Add($"NETCOREAPP{v.Major}_{v.Minor}");
            o.AddRange(coreApp.Where(x => Version.Parse(x) <= v).Select(x => $"NETCOREAPP{x.Replace('.', '_')}_OR_GREATER"));
        }
        else if ((m = Regex.Match(core, @"^netstandard(\d+)\.(\d+)$")).Success)
        {
            var v = new Version(int.Parse(m.Groups[1].Value, CultureInfo.InvariantCulture),
                                int.Parse(m.Groups[2].Value, CultureInfo.InvariantCulture));
            string[] std = { "1.0", "1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "2.0", "2.1" };
            o.Add("NETSTANDARD");
            o.Add($"NETSTANDARD{v.Major}_{v.Minor}");
            o.AddRange(std.Where(x => Version.Parse(x) <= v).Select(x => $"NETSTANDARD{x.Replace('.', '_')}_OR_GREATER"));
        }
        else if ((m = Regex.Match(core, @"^net(\d)(\d)(\d?)$")).Success)
        {
            var v = new Version(int.Parse(m.Groups[1].Value, CultureInfo.InvariantCulture),
                                int.Parse(m.Groups[2].Value, CultureInfo.InvariantCulture),
                                m.Groups[3].Value.Length > 0 ? int.Parse(m.Groups[3].Value, CultureInfo.InvariantCulture) : 0);
            string[] fx = { "2.0", "3.5", "4.0", "4.5", "4.5.1", "4.5.2", "4.6", "4.6.1", "4.6.2", "4.7", "4.7.1", "4.7.2",
                            "4.8", "4.8.1" };
            o.Add("NETFRAMEWORK");
            o.Add("NET" + core[3..]);
            o.AddRange(fx.Where(x => Version.Parse(x.Count(ch => ch == '.') == 1 ? x + ".0" : x) <= v)
                         .Select(x => $"NET{x.Replace(".", "")}_OR_GREATER"));
        }
        return o;
    }

    // ---- the walk -----------------------------------------------------------------------------------------------
    private void Walk(List<string> props, List<string> targets)
    {
        foreach (var f in props)
        {
            _phase = "props";
            ReadFile(f, 0, isProject: false);
        }
        _phase = "project";
        ReadFile(_p.Rel, 0, isProject: true);
        foreach (var f in targets)
        {
            _phase = "targets";
            ReadFile(f, 0, isProject: false);
        }
    }

    private void ReadFile(string rel, int depth, bool isProject)
    {
        var doc = _c.Load(rel);
        if (_record)
        {
            _p.Read.Add(rel);
        }
        if (doc?.Root == null)
        {
            Note($"{rel}: not readable XML");
            return;
        }
        string prev = _thisFile;
        _thisFile = rel;
        _stack.Add(rel);
        if (isProject && _record)
        {
            foreach (var s in (doc.Root.Attribute("Sdk")?.Value ?? "").Split(';', StringSplitOptions.RemoveEmptyEntries |
                                                                               StringSplitOptions.TrimEntries))
            {
                AddSdk(s);
            }
            foreach (var e in doc.Root.Elements().Where(e => e.Name.LocalName == "Sdk"))
            {
                AddSdk(e.Attribute("Name")?.Value ?? "");
            }
        }
        foreach (var el in doc.Root.Elements())
        {
            Element(el, depth);
        }
        _stack.RemoveAt(_stack.Count - 1);
        _thisFile = prev;
    }

    private void AddSdk(string s)
    {
        int slash = s.IndexOf('/');
        string n = (slash >= 0 ? s[..slash] : s).Trim();
        if (n.Length > 0 && !_p.Sdks.Contains(n, StringComparer.OrdinalIgnoreCase))
        {
            _p.Sdks.Add(n);
        }
    }

    private void Element(XElement el, int depth)
    {
        switch (el.Name.LocalName)
        {
            case "PropertyGroup":
                if (!_items && Cond(el))
                {
                    foreach (var prop in el.Elements())
                    {
                        if (Cond(prop))
                        {
                            Assign(prop);
                        }
                    }
                }
                break;
            case "ItemGroup":
                if (_items && Cond(el))
                {
                    foreach (var item in el.Elements())
                    {
                        Item(item);
                    }
                }
                break;
            case "Import":
                Import(el, depth);
                break;
            case "ImportGroup":
                if (Cond(el))
                {
                    foreach (var imp in el.Elements().Where(x => x.Name.LocalName == "Import"))
                    {
                        Import(imp, depth);
                    }
                }
                break;
            case "Choose":
                Note($"{_thisFile}: <Choose> is not evaluated");
                break;
        }
    }

    private void Note(string s)
    {
        if (_record || _items)
        {
            _p.Notes.Add(s);
        }
    }

    private static string? Attr(XElement e, string name) =>
        e.Attributes().FirstOrDefault(a => a.Name.LocalName.Equals(name, StringComparison.OrdinalIgnoreCase))?.Value;

    private void Assign(XElement prop)
    {
        string name = prop.Name.LocalName;
        if (_global.Contains(name))
        {
            return;
        }
        if (!Expand(prop.Value, out string val))
        {
            Note($"{_thisFile}: <{name}> not evaluated: {Short(prop.Value)}");
            return;
        }
        _v[name] = val.Trim();
    }

    private void Item(XElement item)
    {
        string type = item.Name.LocalName;
        if (type is not ("Compile" or "ProjectReference" or "Using" or "InternalsVisibleTo" or "AssemblyAttribute"))
        {
            return;
        }
        if (!Cond(item))
        {
            return;
        }
        string? inc = Attr(item, "Include"), rem = Attr(item, "Remove"), exc = Attr(item, "Exclude");
        string? op = inc != null ? "Include" : rem != null ? "Remove" : null;
        if (op == null)
        {
            return;                                        // Update: metadata only
        }
        if (!Expand(op == "Include" ? inc! : rem!, out string spec))
        {
            Note($"{_thisFile}: <{type} {op}> not evaluated: {Short(op == "Include" ? inc! : rem!)}");
            return;
        }
        string? exclude = null;
        if (exc != null)
        {
            if (!Expand(exc, out string ex))
            {
                Note($"{_thisFile}: <{type} Exclude> not evaluated: {Short(exc)}");
            }
            else
            {
                exclude = ex;
            }
        }
        var meta = new SortedDictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var a in item.Attributes())
        {
            string an = a.Name.LocalName;
            if (an is "Include" or "Remove" or "Exclude" or "Update" or "Condition")
            {
                continue;
            }
            meta[an] = Expand(a.Value, out string av) ? av.Trim() : a.Value.Trim();
        }
        foreach (var child in item.Elements())
        {
            if (Cond(child))
            {
                meta[child.Name.LocalName] = Expand(child.Value, out string cv) ? cv.Trim() : child.Value.Trim();
            }
        }
        _p.Items.Add(new ItemOp(_phase, type, op, spec, exclude, meta, _thisFile));
    }

    private void Import(XElement el, int depth)
    {
        if (!Cond(el))
        {
            return;
        }
        string? sdk = Attr(el, "Sdk");
        if (sdk != null)
        {
            if (_record)
            {
                AddSdk(sdk);
            }
            return;
        }
        string? proj = Attr(el, "Project");
        if (proj == null)
        {
            return;
        }
        if (!ExpandImport(proj, out string path))
        {
            Note($"{_thisFile}: <Import> not evaluated: {Short(proj)}");
            return;
        }
        if (path.Trim().Length == 0)
        {
            Note($"{_thisFile}: <Import> of a file not found above: {Short(proj)}");
            return;
        }
        string? r = P.Resolve(path, P.Dir(_thisFile));
        string? t = r == null || P.IsGlob(r) ? null : _c.Find(r);
        if (t == null)
        {
            Note($"{_thisFile}: <Import> of a file outside the tracked corpus: {Short(proj)}");
            return;
        }
        if (_stack.Contains(t, StringComparer.Ordinal))
        {
            Note($"{_thisFile}: <Import> of a file being read (a cycle, skipped): {t}");
            return;
        }
        if (depth >= 8)
        {
            Note($"{_thisFile}: <Import> nested too deep: {t}");
            return;
        }
        ReadFile(t, depth + 1, isProject: false);
    }

    /// <summary>An Import's Project value: the property functions $([MSBuild]::GetPathOfFileAbove(file[, start])) and
    /// $([MSBuild]::GetDirectoryNameOfFileAbove(start, file)) evaluated (searching the tracked corpus from the start
    /// directory up: the full path, the directory, or "" when not found), then $(...) expanded. False for any other
    /// property function.</summary>
    private bool ExpandImport(string s, out string result)
    {
        const string Fn = "$([MSBuild]::";
        result = "";
        var sb = new StringBuilder();
        int i = 0;
        while (true)
        {
            int k = s.IndexOf(Fn, i, StringComparison.OrdinalIgnoreCase);
            if (k < 0)
            {
                sb.Append(s, i, s.Length - i);
                break;
            }
            sb.Append(s, i, k - i);
            int open = s.IndexOf('(', k + Fn.Length);
            if (open < 0)
            {
                return false;
            }
            string fn = s[(k + Fn.Length)..open].Trim();
            var args = new List<string>();
            int depth = 0, j = open + 1, start = open + 1;
            bool quoted = false;
            for (; j < s.Length; j++)
            {
                char ch = s[j];
                if (ch == '\'')
                {
                    quoted = !quoted;
                }
                else if (quoted)
                {
                }
                else if (ch == '(')
                {
                    depth++;
                }
                else if (ch == ')')
                {
                    if (depth == 0)
                    {
                        break;
                    }
                    depth--;
                }
                else if (ch == ',' && depth == 0)
                {
                    args.Add(s[start..j]);
                    start = j + 1;
                }
            }
            if (j + 1 >= s.Length || s[j + 1] != ')')
            {
                return false;
            }
            args.Add(s[start..j]);
            var vals = new List<string>();
            foreach (var a in args)
            {
                string t = a.Trim();
                if (t.Length >= 2 && t[0] == '\'' && t[^1] == '\'')
                {
                    t = t[1..^1];
                }
                if (!Expand(t, out string v))
                {
                    return false;
                }
                vals.Add(v.Trim());
            }
            string? val = fn.ToLowerInvariant() switch
            {
                "getpathoffileabove" when vals.Count is 1 or 2 =>
                    FileAbove(vals[0], vals.Count == 2 ? vals[1] : P.AsMsBuildDir(P.Dir(_thisFile)), dirOnly: false),
                "getdirectorynameoffileabove" when vals.Count == 2 => FileAbove(vals[1], vals[0], dirOnly: true),
                _ => null,
            };
            if (val == null)
            {
                return false;
            }
            sb.Append(val);
            i = j + 2;
        }
        return Expand(sb.ToString(), out result);
    }

    /// <summary>The tracked `file` in `startDir` or its closest ancestor, as an MSBuild path under P.Root (its directory
    /// when dirOnly); "" when not found; null when the start leaves the corpus.</summary>
    private string? FileAbove(string file, string startDir, bool dirOnly)
    {
        string? dir = P.Resolve(startDir, P.Dir(_thisFile));
        if (dir == null || file.Length == 0 || file.IndexOfAny(new[] { '/', '\\', '*', '?' }) >= 0)
        {
            return null;
        }
        string? found = _c.Nearest(dir, file);
        if (found == null)
        {
            return "";
        }
        return dirOnly ? P.AsMsBuildDir(P.Dir(found)) : P.AsMsBuildDir(P.Dir(found)) + "/" + P.Name(found);
    }

    private static string Short(string s)
    {
        s = Regex.Replace(s.Trim(), @"\s+", " ");
        return s.Length > 160 ? s[..160] : s;
    }

    // ---- $(...) expansion and conditions -------------------------------------------------------------------------
    /// <summary>Expands $(Name) references; false for a property function, a registry / item / metadata reference
    /// (not evaluated here).</summary>
    private bool Expand(string s, out string result)
    {
        var sb = new StringBuilder();
        result = "";
        for (int i = 0; i < s.Length; i++)
        {
            char ch = s[i];
            if ((ch == '@' || ch == '%') && i + 1 < s.Length && s[i + 1] == '(')
            {
                return false;
            }
            if (ch == '$' && i + 1 < s.Length && s[i + 1] == '(')
            {
                int depth = 0, j = i + 1;
                for (; j < s.Length; j++)
                {
                    if (s[j] == '(')
                    {
                        depth++;
                    }
                    else if (s[j] == ')' && --depth == 0)
                    {
                        break;
                    }
                }
                if (j >= s.Length)
                {
                    return false;
                }
                string inner = s[(i + 2)..j].Trim();
                if (!Regex.IsMatch(inner, @"^[A-Za-z_][A-Za-z0-9_\-]*$"))
                {
                    return false;
                }
                if (inner.Equals("MSBuildThisFileDirectory", StringComparison.OrdinalIgnoreCase))
                {
                    sb.Append(P.AsMsBuildDir(P.Dir(_thisFile))).Append('/');
                }
                else if (inner.Equals("MSBuildThisFile", StringComparison.OrdinalIgnoreCase))
                {
                    sb.Append(P.Name(_thisFile));
                }
                else if (inner.Equals("MSBuildThisFileName", StringComparison.OrdinalIgnoreCase))
                {
                    sb.Append(Path.GetFileNameWithoutExtension(P.Name(_thisFile)));
                }
                else
                {
                    sb.Append(Get(inner));
                }
                i = j;
                continue;
            }
            sb.Append(ch);
        }
        result = sb.ToString();
        return true;
    }

    private bool Cond(XElement el)
    {
        string? c = Attr(el, "Condition");
        if (c == null || c.Trim().Length == 0)
        {
            return true;
        }
        bool? v = new CondParser(c, this).Run();
        if (v == null)
        {
            Note($"{_thisFile}: condition not evaluated (taken as false): {Short(c)}");
            return false;
        }
        return v.Value;
    }

    /// <summary>MSBuild conditions: or / and / ! / ( ) / == != &lt; &gt; &lt;= &gt;= over quoted strings, $(...)
    /// and bare words, in three-valued logic (null = not evaluable: a function call or a property function).</summary>
    private sealed class CondParser
    {
        private readonly string _s;
        private readonly MsEval _e;
        private int _i;

        public CondParser(string s, MsEval e)
        {
            _s = s;
            _e = e;
        }

        public bool? Run()
        {
            try
            {
                bool? v = Or();
                Ws();
                return _i == _s.Length ? v : null;
            }
            catch (FormatException)
            {
                return null;
            }
        }

        private void Ws()
        {
            while (_i < _s.Length && char.IsWhiteSpace(_s[_i]))
            {
                _i++;
            }
        }

        private bool Word(string w)
        {
            Ws();
            if (_i + w.Length <= _s.Length && string.Compare(_s, _i, w, 0, w.Length, StringComparison.OrdinalIgnoreCase) == 0 &&
                (_i + w.Length == _s.Length || !char.IsLetterOrDigit(_s[_i + w.Length])))
            {
                _i += w.Length;
                return true;
            }
            return false;
        }

        private bool? Or()
        {
            bool? v = And();
            while (Word("or"))
            {
                bool? r = And();
                v = v == true || r == true ? true : v == false && r == false ? false : null;
            }
            return v;
        }

        private bool? And()
        {
            bool? v = Not();
            while (Word("and"))
            {
                bool? r = Not();
                v = v == false || r == false ? false : v == true && r == true ? true : null;
            }
            return v;
        }

        private bool? Not()
        {
            Ws();
            if (_i < _s.Length && _s[_i] == '!' && (_i + 1 >= _s.Length || _s[_i + 1] != '='))
            {
                _i++;
                bool? v = Not();
                return v == null ? null : !v;
            }
            return Cmp();
        }

        private bool? Cmp()
        {
            Ws();
            if (_i < _s.Length && _s[_i] == '(')
            {
                _i++;
                bool? v = Or();
                Ws();
                if (_i >= _s.Length || _s[_i] != ')')
                {
                    throw new FormatException();
                }
                _i++;
                return v;
            }
            var (ok, a) = Operand();
            Ws();
            string? op = null;
            foreach (var o in new[] { "==", "!=", "<=", ">=", "<", ">" })
            {
                if (string.Compare(_s, _i, o, 0, o.Length, StringComparison.Ordinal) == 0)
                {
                    op = o;
                    _i += o.Length;
                    break;
                }
            }
            if (op == null)
            {
                if (!ok)
                {
                    return null;
                }
                return a.Equals("true", StringComparison.OrdinalIgnoreCase) ? true
                     : a.Equals("false", StringComparison.OrdinalIgnoreCase) ? false : null;
            }
            var (ok2, b) = Operand();
            if (!ok || !ok2)
            {
                return null;
            }
            switch (op)
            {
                case "==":
                    return string.Equals(a, b, StringComparison.OrdinalIgnoreCase);
                case "!=":
                    return !string.Equals(a, b, StringComparison.OrdinalIgnoreCase);
                default:
                    if (!double.TryParse(a, NumberStyles.Float, CultureInfo.InvariantCulture, out double x) ||
                        !double.TryParse(b, NumberStyles.Float, CultureInfo.InvariantCulture, out double y))
                    {
                        return null;
                    }
                    return op switch { "<" => x < y, ">" => x > y, "<=" => x <= y, _ => x >= y };
            }
        }

        private (bool Ok, string Value) Operand()
        {
            Ws();
            if (_i >= _s.Length)
            {
                throw new FormatException();
            }
            if (_s[_i] == '\'')
            {
                int j = _s.IndexOf('\'', _i + 1);
                if (j < 0)
                {
                    throw new FormatException();
                }
                string raw = _s[(_i + 1)..j];
                _i = j + 1;
                return _e.Expand(raw, out string v) ? (true, v) : (false, "");
            }
            if (_s[_i] == '$' && _i + 1 < _s.Length && _s[_i + 1] == '(')
            {
                int depth = 0, j = _i + 1;
                for (; j < _s.Length; j++)
                {
                    if (_s[j] == '(')
                    {
                        depth++;
                    }
                    else if (_s[j] == ')' && --depth == 0)
                    {
                        break;
                    }
                }
                if (j >= _s.Length)
                {
                    throw new FormatException();
                }
                string raw = _s[_i..(j + 1)];
                _i = j + 1;
                return _e.Expand(raw, out string v) ? (true, v) : (false, "");
            }
            int k = _i;
            while (k < _s.Length && (char.IsLetterOrDigit(_s[k]) || _s[k] is '_' or '.' or '-'))
            {
                k++;
            }
            if (k == _i)
            {
                throw new FormatException();
            }
            string word = _s[_i..k];
            _i = k;
            Ws();
            if (_i < _s.Length && _s[_i] == '(')
            {
                // a function (Exists, HasTrailingSlash ...): skip its arguments, not evaluated
                int depth = 0;
                for (; _i < _s.Length; _i++)
                {
                    if (_s[_i] == '(')
                    {
                        depth++;
                    }
                    else if (_s[_i] == ')' && --depth == 0)
                    {
                        _i++;
                        break;
                    }
                }
                return (false, word);
            }
            return (true, word);
        }
    }
}

// ================================================================================ symbols across compilations
internal static class K
{
    public static string Asm(ISymbol s) => s.ContainingAssembly?.Identity.Name ?? "";

    /// <summary>A named type's identity across compilations: (assembly name, documentation-comment id) of its
    /// original definition.</summary>
    public static string Type(INamedTypeSymbol t)
    {
        var d = t.OriginalDefinition;
        return Asm(d) + "|" + (d.GetDocumentationCommentId() ?? d.ToDisplayString(SymbolDisplayFormat.FullyQualifiedFormat) + "`" + d.Arity);
    }

    public static string Method(IMethodSymbol m)
    {
        var d = m.OriginalDefinition;
        return Asm(d) + "|" + (d.GetDocumentationCommentId() ?? d.ToDisplayString(SymbolDisplayFormat.FullyQualifiedFormat));
    }

    /// <summary>A structural key of a type (a type parameter is "!"): two receivers with one key reach the same
    /// dispatch targets.</summary>
    public static string Struct(ITypeSymbol? t) => t switch
    {
        null => "?",
        ITypeParameterSymbol => "!",
        IArrayTypeSymbol a => Struct(a.ElementType) + "[" + a.Rank + "]",
        IPointerTypeSymbol p => Struct(p.PointedAtType) + "*",
        INamedTypeSymbol n when n.TypeKind != TypeKind.Error => Type(n) + "<" + string.Join(",", AllArgs(n).Select(Struct)) + ">",
        _ => "~" + t.ToDisplayString(),
    };

    public static List<ITypeSymbol> AllArgs(INamedTypeSymbol n)
    {
        var chain = new List<INamedTypeSymbol>();
        for (var x = n; x != null; x = x.ContainingType)
        {
            chain.Add(x);
        }
        chain.Reverse();
        return chain.SelectMany(x => x.TypeArguments).ToList();
    }

    /// <summary>Can a value of type `a` be one of type `b` as far as their constructions go: the same original
    /// definition with unifiable type arguments; a type parameter (either side) or dynamic matches anything.</summary>
    public static bool Unify(ITypeSymbol? a, ITypeSymbol? b)
    {
        if (a == null || b == null)
        {
            return false;
        }
        if (a is ITypeParameterSymbol || b is ITypeParameterSymbol || a.TypeKind == TypeKind.Dynamic ||
            b.TypeKind == TypeKind.Dynamic)
        {
            return true;
        }
        if (a is IArrayTypeSymbol aa)
        {
            return b is IArrayTypeSymbol bb && aa.Rank == bb.Rank && Unify(aa.ElementType, bb.ElementType);
        }
        if (a is IPointerTypeSymbol ap)
        {
            return b is IPointerTypeSymbol bp && Unify(ap.PointedAtType, bp.PointedAtType);
        }
        if (a is INamedTypeSymbol na && b is INamedTypeSymbol nb)
        {
            if (na.TypeKind == TypeKind.Error || nb.TypeKind == TypeKind.Error)
            {
                return na.TypeKind == TypeKind.Error && nb.TypeKind == TypeKind.Error && na.Name == nb.Name && na.Arity == nb.Arity;
            }
            if (Type(na) != Type(nb))
            {
                return false;
            }
            var xa = AllArgs(na);
            var xb = AllArgs(nb);
            if (xa.Count != xb.Count)
            {
                return false;
            }
            for (int i = 0; i < xa.Count; i++)
            {
                if (!Unify(xa[i], xb[i]))
                {
                    return false;
                }
            }
            return true;
        }
        return SymbolEqualityComparer.Default.Equals(a, b);
    }

    /// <summary>The type and its base classes, most derived first.</summary>
    public static IEnumerable<INamedTypeSymbol> BaseChain(INamedTypeSymbol t)
    {
        for (var x = t; x != null; x = x.BaseType)
        {
            yield return x;
        }
    }

    public static IMethodSymbol Root(IMethodSymbol m)
    {
        var x = m.OriginalDefinition;
        for (int guard = 0; x.OverriddenMethod != null && guard < 64; guard++)
        {
            x = x.OverriddenMethod.OriginalDefinition;
        }
        return x;
    }
}

internal sealed class Unit
{
    public required string Id;                             // the csproj path, or <leftover>
    public ProjectInfo? Project;
    public required List<string> Files;
    public required CSharpCompilation Compilation;
    public required Dictionary<string, SyntaxTree> Trees;  // rel -> tree
    public MetadataReference? Ref;
    public JsonArray Defines = new();
    public JsonArray References = new();
}

internal sealed class Ctx
{
    public required Dictionary<SyntaxTree, string> Rel;
    public required Dictionary<string, LfLines> Lines;
    public required Dictionary<string, List<(INamedTypeSymbol T, List<INamedTypeSymbol> Views)>> Implementers;
    public required Dictionary<string, List<(IMethodSymbol M, INamedTypeSymbol T)>> Overrides;
    public required Dictionary<string, HashSet<string>> GeneratedFriends;   // corpus assembly -> MSBuild-generated friends
    public readonly ConcurrentDictionary<string, (string? Rule, string? None, JsonArray Targets, JsonArray PreAc1)> DispatchCache = new(StringComparer.Ordinal);
}

internal static class Program
{
    private const string Schema = "call-linking/roslyn-sites/2";
    private const string WantRoslyn = "4.8.0";
    private const string PackVersion = "8.0.3";
    private static readonly string[] PackNames = { "Microsoft.NETCore.App.Ref", "Microsoft.AspNetCore.App.Ref" };

    // the SDK 8.0.202 implicit-using lists (Microsoft.NET.Sdk.CSharp.props; Sdk.Server.props (.Web);
    // Microsoft.NET.Sdk.Worker.props; Microsoft.NET.Sdk.BlazorWebAssembly Sdk.props). Microsoft.NET.Sdk.Razor adds none.
    private static readonly string[] UsingsBase =
        { "System", "System.Collections.Generic", "System.IO", "System.Linq", "System.Net.Http", "System.Threading",
          "System.Threading.Tasks" };
    private static readonly string[] UsingsWeb =
        { "System.Net.Http.Json", "Microsoft.AspNetCore.Builder", "Microsoft.AspNetCore.Hosting",
          "Microsoft.AspNetCore.Http", "Microsoft.AspNetCore.Routing", "Microsoft.Extensions.Configuration",
          "Microsoft.Extensions.DependencyInjection", "Microsoft.Extensions.Hosting", "Microsoft.Extensions.Logging" };
    private static readonly string[] UsingsWorker =
        { "Microsoft.Extensions.Configuration", "Microsoft.Extensions.DependencyInjection",
          "Microsoft.Extensions.Hosting", "Microsoft.Extensions.Logging" };
    private static readonly string[] UsingsBlazorWasm =
        { "Microsoft.Extensions.Configuration", "Microsoft.Extensions.DependencyInjection",
          "Microsoft.Extensions.Logging" };

    private static readonly SymbolDisplayFormat TypeFmt = new(
        globalNamespaceStyle: SymbolDisplayGlobalNamespaceStyle.Omitted,
        typeQualificationStyle: SymbolDisplayTypeQualificationStyle.NameAndContainingTypesAndNamespaces,
        genericsOptions: SymbolDisplayGenericsOptions.IncludeTypeParameters,
        miscellaneousOptions: SymbolDisplayMiscellaneousOptions.ExpandNullable);

    private static readonly JsonSerializerOptions JsonOpt = new()
    {
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        WriteIndented = false,
        TypeInfoResolver = new System.Text.Json.Serialization.Metadata.DefaultJsonTypeInfoResolver(),
    };

    private static int Main(string[] argv)
    {
        Console.OutputEncoding = new UTF8Encoding(false);
        Options? opt = ParseArgs(argv);
        if (opt == null)
        {
            Console.Error.WriteLine("usage: TruthRoslyn <corpus> <out.jsonl> --tracked=<file> --file-list=git|listed " +
                                    "[--threads=N] [--record-sdk=VERSION] [--record-head=SHA] [--record-tool=SHA256]");
            return 2;
        }
        string roslyn = InfoVersion(typeof(CSharpCompilation).Assembly);
        string roslynCommon = InfoVersion(typeof(Compilation).Assembly);
        if (!roslyn.StartsWith(WantRoslyn, StringComparison.Ordinal) || !roslynCommon.StartsWith(WantRoslyn, StringComparison.Ordinal))
        {
            Console.Error.WriteLine($"[truth_roslyn] REFUSED: Roslyn {roslyn} / {roslynCommon} (the registered reference is {WantRoslyn})");
            return 2;
        }
        if (!Directory.Exists(opt.Root))
        {
            Console.Error.WriteLine($"[truth_roslyn] no such corpus directory: {opt.Root}");
            return 2;
        }
        var sw = Stopwatch.StartNew();
        var po = new ParallelOptions { MaxDegreeOfParallelism = Math.Max(1, opt.Threads) };

        // ---- the tracked files ----------------------------------------------------------------------------------
        var listed = File.ReadAllText(opt.Tracked, new UTF8Encoding(false)).Split('\0', '\n')
                         .Select(x => x.Trim('\r').Replace('\\', '/')).Where(x => x.Length > 0)
                         .Distinct(StringComparer.Ordinal).OrderBy(x => x, StringComparer.Ordinal).ToList();
        var tracked = listed.Where(x => File.Exists(Path.Combine(opt.Root, x))).ToList();
        int notOnDisk = listed.Count - tracked.Count;
        var trackedCi = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (var t in tracked)
        {
            trackedCi.TryAdd(t.ToLowerInvariant(), t);
        }
        var corpus = new Corpus
        {
            Root = opt.Root, Tracked = tracked, TrackedSet = new HashSet<string>(tracked, StringComparer.Ordinal),
            TrackedCi = trackedCi,
            Cs = tracked.Where(x => x.EndsWith(".cs", StringComparison.OrdinalIgnoreCase)).ToList(),
        };

        // ---- the reference packs ----------------------------------------------------------------------------------
        var packRecords = new JsonArray();
        var packRefs = LoadPacks(packRecords, out string? packError);
        if (packError != null)
        {
            Console.Error.WriteLine($"[truth_roslyn] REFUSED: {packError}");
            return 2;
        }

        // ---- the projects -----------------------------------------------------------------------------------------
        var projects = tracked.Where(x => x.EndsWith(".csproj", StringComparison.OrdinalIgnoreCase))
                              .Select(x => new ProjectInfo { Rel = x, Dir = P.Dir(x), Name = Path.GetFileNameWithoutExtension(P.Name(x)) })
                              .ToList();
        foreach (var p in projects)
        {
            MsEval.Evaluate(corpus, p);
        }
        var projectDirs = projects.Select(p => p.Dir).Distinct(StringComparer.Ordinal)
                                  .OrderByDescending(d => d.Length).ThenBy(d => d, StringComparer.Ordinal).ToList();
        string? ClaimDir(string f) => projectDirs.FirstOrDefault(d => P.Under(f, d));
        var byRel = projects.ToDictionary(p => p.Rel, StringComparer.Ordinal);
        foreach (var p in projects)
        {
            ApplyItems(corpus, p, ClaimDir);
        }
        var order = TopoOrder(projects, byRel);
        foreach (var p in projects)
        {
            p.Closure.AddRange(Closure(p, byRel, order));
        }

        // ---- the compilations ------------------------------------------------------------------------------------
        var heldBy = new Dictionary<string, List<string>>(StringComparer.Ordinal);
        foreach (var p in projects)
        {
            foreach (var f in p.Files)
            {
                if (!heldBy.TryGetValue(f, out var l))
                {
                    heldBy[f] = l = new List<string>();
                }
                l.Add(p.Rel);
            }
        }
        var leftoverFiles = corpus.Cs.Where(f => !heldBy.ContainsKey(f) && ClaimDir(f) == null).ToList();
        var uncompiled = new JsonArray();
        foreach (var f in corpus.Cs.Where(x => !heldBy.ContainsKey(x) && ClaimDir(x) != null))
        {
            string cd = ClaimDir(f)!;
            var owners = projects.Where(x => x.Dir == cd).ToList();
            string why = owners.Any(x => x.Removed.Contains(f)) ? "compile_remove"
                       : owners.Any(x => x.MauiRemoved.Contains(f)) ? "maui_platform"
                       : BinObj(f, cd) ? "bin_obj" : "not_included";
            uncompiled.Add(new JsonArray(f, why));
        }

        var texts = new ConcurrentDictionary<string, SourceText>(StringComparer.Ordinal);
        var needed = projects.SelectMany(p => p.Files).Concat(leftoverFiles).Distinct(StringComparer.Ordinal).ToList();
        Parallel.ForEach(needed, po, f =>
        {
            using var fs = File.OpenRead(Path.Combine(opt.Root, f));
            texts[f] = SourceText.From(fs, encoding: null, checksumAlgorithm: SourceHashAlgorithm.Sha256,
                                       throwIfBinaryDetected: false);
        });
        var lines = texts.ToDictionary(kv => kv.Key, kv => new LfLines(kv.Value), StringComparer.Ordinal);

        // one tree per (file, preprocessor symbols), shared by the compilations that agree on them
        var treeCache = new ConcurrentDictionary<string, SyntaxTree>(StringComparer.Ordinal);
        CSharpParseOptions Parse(IEnumerable<string> defs) =>
            new(LanguageVersion.CSharp12, DocumentationMode.None, SourceCodeKind.Regular, defs);
        var jobs = new List<(string File, CSharpParseOptions Opts, string Key)>();
        var projectOpts = new Dictionary<string, CSharpParseOptions>(StringComparer.Ordinal);
        foreach (var p in projects)
        {
            var o = Parse(p.Defines);
            projectOpts[p.Rel] = o;
            string dk = string.Join(";", p.Defines);
            jobs.AddRange(p.Files.Select(f => (f, o, f + "\0" + dk)));
        }
        var leftoverOpts = Parse(Array.Empty<string>());
        jobs.AddRange(leftoverFiles.Select(f => (f, leftoverOpts, f + "\0")));
        Parallel.ForEach(jobs.DistinctBy(j => j.Key).ToList(), po, j =>
        {
            treeCache[j.Key] = CSharpSyntaxTree.ParseText(texts[j.File], j.Opts, path: j.File);
        });

        var units = new List<Unit>();
        var unitOf = new Dictionary<string, Unit>(StringComparer.Ordinal);
        foreach (var p in order)
        {
            string dk = string.Join(";", p.Defines);
            var trees = p.Files.ToDictionary(f => f, f => treeCache[f + "\0" + dk], StringComparer.Ordinal);
            var all = p.Files.Select(f => trees[f]).ToList();
            var usingLines = p.UsingLines;
            if (usingLines.Count > 0)
            {
                all.Add(CSharpSyntaxTree.ParseText(string.Join("\n", usingLines) + "\n", projectOpts[p.Rel],
                                                   path: "<generated>/" + p.Rel + ".GlobalUsings.g.cs"));
            }
            if (p.Ivt.Count > 0)                     // AC3: the AssemblyInfo source WriteCodeFragment writes (its IVT lines)
            {
                all.Add(CSharpSyntaxTree.ParseText(
                    string.Join("\n", p.Ivt.Select(x => "[assembly: global::System.Runtime.CompilerServices." +
                                                        $"InternalsVisibleTo({SymbolDisplay.FormatLiteral(x, true)})]")) + "\n",
                    projectOpts[p.Rel], path: "<generated>/" + p.Rel + ".AssemblyInfo.g.cs"));
            }
            var refs = new List<MetadataReference>(packRefs);
            var refRecords = new JsonArray();
            foreach (var pr in PackNames)
            {
                refRecords.Add($"pack:{pr}/{PackVersion}");
            }
            foreach (var c in p.Closure)
            {
                refs.Add(unitOf[c].Ref!);
                refRecords.Add("project:" + c);
            }
            var options = new CSharpCompilationOptions(Enum.Parse<OutputKind>(p.OutputKind), allowUnsafe: true,
                                                       nullableContextOptions: Enum.Parse<NullableContextOptions>(p.Nullable),
                                                       concurrentBuild: true, deterministic: true);
            var comp = CSharpCompilation.Create(p.AssemblyName, all, refs, options);
            var u = new Unit
            {
                Id = p.Rel, Project = p, Files = p.Files, Compilation = comp, Trees = trees, Ref = comp.ToMetadataReference(),
                Defines = new JsonArray(p.Defines.Select(d => (JsonNode)JsonValue.Create(d)!).ToArray()),
                References = refRecords,
            };
            units.Add(u);
            unitOf[p.Rel] = u;
        }
        Unit? leftover = null;
        if (leftoverFiles.Count > 0)
        {
            var trees = leftoverFiles.ToDictionary(f => f, f => treeCache[f + "\0"], StringComparer.Ordinal);
            var refs = new List<MetadataReference>(packRefs);
            var refRecords = new JsonArray();
            foreach (var pr in PackNames)
            {
                refRecords.Add($"pack:{pr}/{PackVersion}");
            }
            foreach (var u in units)
            {
                refs.Add(u.Ref!);
                refRecords.Add("project:" + u.Id);
            }
            var comp = CSharpCompilation.Create("TruthRoslyn.Leftover", leftoverFiles.Select(f => trees[f]), refs,
                new CSharpCompilationOptions(OutputKind.DynamicallyLinkedLibrary, allowUnsafe: true, concurrentBuild: true,
                                             deterministic: true));
            leftover = new Unit
            {
                Id = "<leftover>", Files = leftoverFiles, Compilation = comp, Trees = trees, References = refRecords,
            };
            units.Add(leftover);
        }
        int retargeted = 0;
        foreach (var u in units)
        {
            foreach (var c in u.Project?.Closure ?? (leftover == u ? units.Where(x => x != u).Select(x => x.Id).ToList() : new List<string>()))
            {
                var r = unitOf[c];
                if (!ReferenceEquals(u.Compilation.GetAssemblyOrModuleSymbol(r.Ref!), r.Compilation.Assembly))
                {
                    retargeted++;
                }
            }
        }

        // ---- which compilation gives each file its rows ----------------------------------------------------------
        var ownerOf = new Dictionary<string, Unit>(StringComparer.Ordinal);
        var shared = new JsonArray();
        foreach (var f in corpus.Cs)
        {
            if (heldBy.TryGetValue(f, out var holders))
            {
                string? cd = ClaimDir(f);
                var sorted = holders.OrderBy(h => h, StringComparer.Ordinal).ToList();
                string own = sorted.FirstOrDefault(h => byRel[h].Dir == cd) ?? sorted[0];
                ownerOf[f] = unitOf[own];
                if (sorted.Count > 1)
                {
                    shared.Add(new JsonArray(f, own, new JsonArray(sorted.Select(h => (JsonNode)JsonValue.Create(h)!).ToArray())));
                }
            }
            else if (leftover != null && leftover.Trees.ContainsKey(f))
            {
                ownerOf[f] = leftover;
            }
        }
        var rel = new Dictionary<SyntaxTree, string>();
        foreach (var u in units)
        {
            foreach (var kv in u.Trees)
            {
                rel[kv.Value] = kv.Key;
            }
        }

        // ---- every source type, for the dispatch rules -----------------------------------------------------------
        var typeJobs = units.SelectMany(u => u.Trees.Values.Select(t => (u, t))).ToList();
        var declared = new List<INamedTypeSymbol>[typeJobs.Count];
        Parallel.For(0, typeJobs.Count, po, i =>
        {
            var (u, t) = typeJobs[i];
            var model = u.Compilation.GetSemanticModel(t);
            var l = new List<INamedTypeSymbol>();
            foreach (var td in t.GetRoot().DescendantNodes().OfType<TypeDeclarationSyntax>())
            {
                if (model.GetDeclaredSymbol(td) is INamedTypeSymbol s)
                {
                    l.Add(s);
                }
            }
            declared[i] = l;
        });
        var implementers = new Dictionary<string, List<(INamedTypeSymbol, List<INamedTypeSymbol>)>>(StringComparer.Ordinal);
        var overrides = new Dictionary<string, List<(IMethodSymbol, INamedTypeSymbol)>>(StringComparer.Ordinal);
        var seenTypes = new HashSet<INamedTypeSymbol>(SymbolEqualityComparer.Default);
        int nSourceTypes = 0;
        foreach (var t in declared.SelectMany(x => x))
        {
            if (!seenTypes.Add(t))
            {
                continue;
            }
            nSourceTypes++;
            if (t.TypeKind is not (TypeKind.Class or TypeKind.Struct))
            {
                continue;
            }
            foreach (var g in t.AllInterfaces.GroupBy(i => K.Type(i)).OrderBy(g => g.Key, StringComparer.Ordinal))
            {
                if (!implementers.TryGetValue(g.Key, out var l))
                {
                    implementers[g.Key] = l = new List<(INamedTypeSymbol, List<INamedTypeSymbol>)>();
                }
                l.Add((t, g.ToList()));
            }
            foreach (var m in t.GetMembers().OfType<IMethodSymbol>())
            {
                if (m.IsOverride && m.MethodKind == MethodKind.Ordinary && !m.IsImplicitlyDeclared &&
                    m.DeclaringSyntaxReferences.Any(r => rel.ContainsKey(r.SyntaxTree)))
                {
                    string key = K.Method(K.Root(m));
                    if (!overrides.TryGetValue(key, out var l))
                    {
                        overrides[key] = l = new List<(IMethodSymbol, INamedTypeSymbol)>();
                    }
                    l.Add((m, t));
                }
            }
        }
        var friends = new Dictionary<string, HashSet<string>>(StringComparer.Ordinal);    // assembly -> generated friends
        foreach (var p in projects)
        {
            if (!friends.TryGetValue(p.AssemblyName, out var fs))
            {
                friends[p.AssemblyName] = fs = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            }
            foreach (var x in p.Ivt)
            {
                fs.Add(x.Split(',')[0].Trim());
            }
        }
        var ctx = new Ctx
        {
            Rel = rel, Lines = lines, Implementers = implementers, Overrides = overrides, GeneratedFriends = friends,
        };

        // ---- the call sites --------------------------------------------------------------------------------------
        var files = ownerOf.Keys.OrderBy(f => f, StringComparer.Ordinal).ToList();
        var perFile = new List<(int, int, string, int, int, int, int, JsonObject)>[files.Count];
        var nameofs = new int[files.Count];
        var errors = new int[files.Count];
        Parallel.For(0, files.Count, po, i =>
        {
            var u = ownerOf[files[i]];
            var tree = u.Trees[files[i]];
            var model = u.Compilation.GetSemanticModel(tree);
            var outRows = new List<(int, int, string, int, int, int, int, JsonObject)>();
            foreach (var node in tree.GetRoot().DescendantNodes())
            {
                if (node is InvocationExpressionSyntax inv)
                {
                    if (IsNameOf(inv, model))
                    {
                        nameofs[i]++;
                        continue;
                    }
                    outRows.Add(Safe(() => Site(inv, model, u, ctx), inv, u, ctx, ref errors[i]));
                }
                else if (node is BaseObjectCreationExpressionSyntax oc)
                {
                    outRows.Add(Safe(() => Site(oc, model, u, ctx), oc, u, ctx, ref errors[i]));
                }
            }
            outRows.Sort((a, b) =>
            {
                int c = a.Item1.CompareTo(b.Item1);
                if (c == 0) c = a.Item2.CompareTo(b.Item2);
                if (c == 0) c = string.CompareOrdinal(a.Item3, b.Item3);
                if (c == 0) c = a.Item4.CompareTo(b.Item4);
                if (c == 0) c = a.Item5.CompareTo(b.Item5);
                if (c == 0) c = a.Item6.CompareTo(b.Item6);
                if (c == 0) c = a.Item7.CompareTo(b.Item7);
                return c;
            });
            perFile[i] = outRows;
        });

        // ---- diagnostics and counts ------------------------------------------------------------------------------
        var parseErrors = new JsonArray();
        for (int i = 0; i < files.Count; i++)
        {
            var tree = ownerOf[files[i]].Trees[files[i]];
            var errs = tree.GetDiagnostics().Where(d => d.Severity == DiagnosticSeverity.Error)
                           .OrderBy(d => d.Location.SourceSpan.Start).ThenBy(d => d.Id, StringComparer.Ordinal).ToList();
            if (errs.Count > 0)
            {
                var lf = lines[files[i]];
                parseErrors.Add(new JsonArray(files[i], errs.Count,
                    $"{lf.Line(errs[0].Location.SourceSpan.Start)}: {errs[0].Id} {errs[0].GetMessage(CultureInfo.InvariantCulture)}"));
            }
        }
        var rowsByProject = new Dictionary<string, int>(StringComparer.Ordinal);
        for (int i = 0; i < files.Count; i++)
        {
            rowsByProject[ownerOf[files[i]].Id] = rowsByProject.GetValueOrDefault(ownerOf[files[i]].Id) + perFile[i].Count;
        }
        var counts = new SortedDictionary<string, int>(StringComparer.Ordinal);
        void Count(string k, int n = 1) => counts[k] = counts.GetValueOrDefault(k) + n;
        foreach (var row in perFile.SelectMany(l => l).Select(x => x.Item8))
        {
            Count("sites");
            Count("site_kind:" + row["site_kind"]!.GetValue<string>());
            Count("form:" + row["form"]!.GetValue<string>());
            Count("resolution:" + row["resolution"]!.GetValue<string>());
            var t = row["target"] as JsonObject;
            if (t != null && t["decls"] is JsonArray d && d.Count > 0)
            {
                Count("bound_in_corpus");
            }
            else if (t != null)
            {
                Count("bound_external");
            }
            string? rule = row["dispatch_rule"]?.GetValue<string>();
            if (rule != null)
            {
                Count("dispatch_rule:" + rule);
                int n = ((JsonArray)row["dispatch_raw"]!).Count;
                if (n > 0)
                {
                    Count("dispatch_raw_rows:" + rule);
                    Count("dispatch_raw_targets:" + rule, n);
                }
            }
            if (row["dispatch_none"]?.GetValue<string>() is string none)
            {
                Count("dispatch_none:" + none);
            }
            int na = ((JsonArray)row["dispatch_ac1_added"]!).Count;
            if (na > 0)
            {
                Count("ac1_rows:interface");
                Count("ac1_targets:interface", na);
            }
            if (row["receiver_reading"]?.GetValue<string>() is string rr)
            {
                Count("override_type_parameter_receiver:" + rr);
            }
            if (row["internals_visible_to"]?.GetValue<string>() is string iv)
            {
                Count("ivt_sites:" + iv);
            }
            if (row["error"] != null)
            {
                Count("resolver_errors");
            }
        }
        counts["nameof_skipped"] = nameofs.Sum();
        counts["maui_platform_files_excluded"] = projects.SelectMany(p => p.MauiRemoved).Distinct(StringComparer.Ordinal).Count();

        var projectRecords = new JsonArray();
        foreach (var p in projects)
        {
            var u = unitOf[p.Rel];
            var decl = u.Compilation.GetDeclarationDiagnostics().Where(d => d.Severity == DiagnosticSeverity.Error)
                        .GroupBy(d => d.Id).OrderBy(g => g.Key, StringComparer.Ordinal)
                        .Select(g => KeyValuePair.Create(g.Key, (JsonNode?)g.Count()));
            projectRecords.Add(new JsonObject
            {
                ["path"] = p.Rel,
                ["assembly_name"] = p.AssemblyName,
                ["sdk"] = Arr(p.Sdks),
                ["target_framework"] = p.FirstTf,
                ["defines"] = u.Defines.DeepClone(),
                ["implicit_usings"] = p.ImplicitUsings,
                ["global_usings"] = Arr(p.UsingLines),
                ["output_kind"] = p.OutputKind,
                ["nullable"] = p.Nullable,
                ["project_references"] = Arr(p.DirectRefs),
                ["skipped_references"] = new JsonArray(p.SkippedRefs.Select(x => x.DeepClone()).ToArray()),
                ["transitive_references"] = p.TransitiveRefs,
                ["references"] = u.References.DeepClone(),
                ["files"] = p.Files.Count,
                ["linked_files"] = Arr(p.Links),
                ["removed_files"] = Arr(p.Removed),
                ["use_maui"] = p.UseMaui,
                ["maui_removed_files"] = Arr(p.MauiRemoved),
                ["generate_assembly_info"] = p.GenerateAssemblyInfo,
                ["internals_visible_to"] = Arr(p.Ivt),
                ["rows"] = rowsByProject.GetValueOrDefault(p.Rel),
                ["declaration_errors"] = new JsonObject(decl),
                ["read"] = Arr(p.Read.Distinct(StringComparer.Ordinal)),
                ["notes"] = Arr(p.Notes),
            });
        }
        JsonObject? leftoverRecord = leftover == null ? null : new JsonObject
        {
            ["assembly_name"] = "TruthRoslyn.Leftover",
            ["defines"] = new JsonArray(),
            ["implicit_usings"] = false,
            ["references"] = leftover.References.DeepClone(),
            ["files"] = Arr(leftover.Files),
            ["rows"] = rowsByProject.GetValueOrDefault(leftover.Id),
        };

        var header = new JsonObject
        {
            ["schema"] = Schema,
            ["kind"] = "header",
            ["contract"] = "PREREG_CS.md 8.2 (the reference contract)",
            ["resolver"] = new JsonObject
            {
                ["name"] = "roslyn",
                ["microsoft_codeanalysis"] = roslynCommon,
                ["microsoft_codeanalysis_csharp"] = roslyn,
                ["packages"] = Packages(),
                ["runtime"] = Environment.Version.ToString(),
                ["sdk"] = opt.Sdk,
                ["tool_sources_sha256"] = opt.Tool,
                ["language_version"] = LanguageVersion.CSharp12.ToString(),
                ["line_rule"] = "lf",
                ["column_rule"] = "utf16",
            },
            ["corpus"] = new JsonObject
            {
                ["head"] = opt.Head,
                ["file_list"] = opt.FileList,
                ["tracked_files"] = tracked.Count,
                ["tracked_not_on_disk"] = notOnDisk,
                ["tracked_cs"] = corpus.Cs.Count,
                ["tracked_csproj"] = projects.Count,
            },
            ["packs"] = packRecords,
            ["projects"] = projectRecords,
            ["leftover"] = leftoverRecord,
            ["shared_files"] = shared,
            ["uncompiled_files"] = uncompiled,
            ["xml_errors"] = new JsonObject(corpus.XmlErrors.Select(kv => KeyValuePair.Create(kv.Key, (JsonNode?)kv.Value))),
            ["retargeted_references"] = retargeted,
            ["n_source_types"] = nSourceTypes,
            ["n_files"] = files.Count,
            ["n_parse_errors"] = parseErrors.Count,
            ["parse_errors"] = parseErrors,
            ["counts"] = new JsonObject(counts.Select(kv => KeyValuePair.Create(kv.Key, (JsonNode?)kv.Value))),
        };

        string? dir = Path.GetDirectoryName(Path.GetFullPath(opt.Out));
        if (!string.IsNullOrEmpty(dir))
        {
            Directory.CreateDirectory(dir);
        }
        using (var w = new StreamWriter(opt.Out, false, new UTF8Encoding(false)) { NewLine = "\n" })
        {
            w.Write(header.ToJsonString(JsonOpt));
            w.Write('\n');
            for (int i = 0; i < files.Count; i++)
            {
                foreach (var r in perFile[i])
                {
                    w.Write(r.Item8.ToJsonString(JsonOpt));
                    w.Write('\n');
                }
            }
        }
        Console.Error.WriteLine(
            $"[truth_roslyn] projects {projects.Count} (+{(leftover == null ? 0 : 1)} leftover) files {files.Count} " +
            $"(parse errors {parseErrors.Count}, uncompiled {uncompiled.Count}) sites {counts.GetValueOrDefault("sites")} " +
            $"bound in corpus {counts.GetValueOrDefault("bound_in_corpus")} resolver errors " +
            $"{counts.GetValueOrDefault("resolver_errors")} retargeted refs {retargeted} in {sw.Elapsed.TotalSeconds:F1}s");
        return 0;
    }

    private static JsonArray Arr(IEnumerable<string> xs) => new(xs.Select(x => (JsonNode)JsonValue.Create(x)!).ToArray());

    // ================================================================================================ arguments
    private static Options? ParseArgs(string[] argv)
    {
        var opt = new Options();
        var pos = new List<string>();
        foreach (var a in argv)
        {
            if (!a.StartsWith("--", StringComparison.Ordinal))
            {
                pos.Add(a);
                continue;
            }
            int eq = a.IndexOf('=');
            string k = eq < 0 ? a : a[..eq];
            string? v = eq < 0 ? null : a[(eq + 1)..];
            switch (k)
            {
                case "--tracked" when !string.IsNullOrEmpty(v): opt.Tracked = v; break;
                case "--file-list" when v is "git" or "listed": opt.FileList = v == "git" ? "git ls-files" : "listed"; break;
                case "--threads" when int.TryParse(v, out int n) && n > 0: opt.Threads = n; break;
                case "--record-sdk" when !string.IsNullOrEmpty(v): opt.Sdk = v; break;
                case "--record-head" when !string.IsNullOrEmpty(v): opt.Head = v; break;
                case "--record-tool" when !string.IsNullOrEmpty(v): opt.Tool = v; break;
                default:
                    Console.Error.WriteLine($"[truth_roslyn] unknown or malformed option: {a}");
                    return null;
            }
        }
        if (pos.Count != 2 || opt.Tracked.Length == 0 || opt.FileList.Length == 0)
        {
            return null;
        }
        opt.Root = Path.GetFullPath(pos[0]).TrimEnd('\\', '/');
        opt.Out = pos[1];
        return opt;
    }

    private static string InfoVersion(Assembly asm)
    {
        string? info = asm.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion;
        if (info == null)
        {
            return asm.GetName().Version?.ToString() ?? "unknown";
        }
        int plus = info.IndexOf('+');
        return plus < 0 ? info : info[..plus];
    }

    /// <summary>The restored package closure the tool runs with (its own deps.json).</summary>
    private static JsonObject Packages()
    {
        var o = new JsonObject();
        try
        {
            string deps = Path.Combine(AppContext.BaseDirectory, "TruthRoslyn.deps.json");
            var doc = JsonNode.Parse(File.ReadAllText(deps)) as JsonObject;
            if (doc?["libraries"] is JsonObject libs)
            {
                foreach (var kv in libs.OrderBy(kv => kv.Key, StringComparer.Ordinal))
                {
                    if ((kv.Value as JsonObject)?["type"]?.GetValue<string>() == "package")
                    {
                        int slash = kv.Key.IndexOf('/');
                        o[kv.Key[..slash]] = kv.Key[(slash + 1)..];
                    }
                }
            }
        }
        catch (Exception exc)
        {
            o["error"] = exc.GetType().Name;
        }
        return o;
    }

    private static List<MetadataReference> LoadPacks(JsonArray records, out string? error)
    {
        error = null;
        var outList = new List<MetadataReference>();
        var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        string runtimeDir = System.Runtime.InteropServices.RuntimeEnvironment.GetRuntimeDirectory();
        string dotnetRoot = Path.GetFullPath(Path.Combine(runtimeDir, "..", "..", ".."));
        foreach (var pack in PackNames)
        {
            string dir = Path.Combine(dotnetRoot, "packs", pack, PackVersion, "ref", "net8.0");
            if (!Directory.Exists(dir))
            {
                error = $"the reference pack {pack} {PackVersion} is not installed under {Path.Combine(dotnetRoot, "packs")}";
                return outList;
            }
            using var sha = SHA256.Create();
            int n = 0, dup = 0;
            foreach (var f in Directory.EnumerateFiles(dir, "*.dll").OrderBy(f => Path.GetFileName(f), StringComparer.Ordinal))
            {
                string name = Path.GetFileName(f);
                byte[] bytes = File.ReadAllBytes(f);
                byte[] nb = Encoding.UTF8.GetBytes(name);
                sha.TransformBlock(nb, 0, nb.Length, null, 0);
                sha.TransformBlock(new byte[] { 0 }, 0, 1, null, 0);
                sha.TransformBlock(bytes, 0, bytes.Length, null, 0);
                sha.TransformBlock(new byte[] { 0 }, 0, 1, null, 0);
                if (!names.Add(Path.GetFileNameWithoutExtension(name)))
                {
                    dup++;
                    continue;
                }
                outList.Add(MetadataReference.CreateFromImage(bytes, filePath: f));
                n++;
            }
            sha.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
            records.Add(new JsonObject
            {
                ["pack"] = pack, ["version"] = PackVersion, ["assemblies"] = n, ["duplicates_skipped"] = dup,
                ["sha256"] = Convert.ToHexString(sha.Hash!).ToLowerInvariant(),
            });
        }
        return outList;
    }

    // ================================================================================ items, references, order
    private static bool BinObj(string f, string dir)
    {
        string r = dir.Length == 0 ? f : f[(dir.Length + 1)..];
        return r.StartsWith("bin/", StringComparison.OrdinalIgnoreCase) || r.StartsWith("obj/", StringComparison.OrdinalIgnoreCase);
    }

    private static IEnumerable<string> SdkUsings(ProjectInfo p)
    {
        if (p.Sdks.Count == 0)
        {
            yield break;
        }
        foreach (var u in UsingsBase)
        {
            yield return u;
        }
        bool Has(string s) => p.Sdks.Any(x => x.Equals(s, StringComparison.OrdinalIgnoreCase));
        if (Has("Microsoft.NET.Sdk.Web"))
        {
            foreach (var u in UsingsWeb)
            {
                yield return u;
            }
        }
        if (Has("Microsoft.NET.Sdk.Worker"))
        {
            foreach (var u in UsingsWorker)
            {
                yield return u;
            }
        }
        if (Has("Microsoft.NET.Sdk.BlazorWebAssembly"))
        {
            foreach (var u in UsingsBlazorWasm)
            {
                yield return u;
            }
        }
    }

    private static List<string> MatchCs(Corpus c, ProjectInfo p, string spec, string phaseNote)
    {
        var outList = new List<string>();
        foreach (var piece in spec.Split(';', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
        {
            string? r = P.Resolve(piece, p.Dir);
            if (r == null)
            {
                p.Notes.Add($"{phaseNote} outside the corpus: {piece}");
                continue;
            }
            if (P.IsGlob(r))
            {
                var rx = P.Glob(r);
                outList.AddRange(c.Cs.Where(f => rx.IsMatch(f)));
            }
            else
            {
                string? t = c.Find(r);
                if (t != null && t.EndsWith(".cs", StringComparison.OrdinalIgnoreCase))
                {
                    outList.Add(t);
                }
                else if (t == null && r.EndsWith(".cs", StringComparison.OrdinalIgnoreCase))
                {
                    p.Notes.Add($"{phaseNote} not a tracked file: {piece}");
                }
            }
        }
        return outList;
    }

    private static readonly string[] IvtAttributeNames =
        { "System.Runtime.CompilerServices.InternalsVisibleTo", "System.Runtime.CompilerServices.InternalsVisibleToAttribute" };

    /// <summary>The MAUI single-project platform folders (Microsoft.Maui.Controls.SingleProject.targets:
    /// $(PlatformsProjectFolder) and each MauiPlatformSpecificFolder), by target platform identifier.</summary>
    private static readonly Dictionary<string, string> MauiFolders = new(StringComparer.Ordinal)
    {
        ["android"] = "Android", ["ios"] = "iOS", ["maccatalyst"] = "MacCatalyst", ["windows"] = "Windows",
        ["tizen"] = "Tizen",
    };

    private static void ApplyItems(Corpus c, ProjectInfo p, Func<string, string?> claimDir)
    {
        var compile = new List<string>();
        var set = new HashSet<string>(StringComparer.Ordinal);
        var removed = new SortedSet<string>(StringComparer.Ordinal);
        var usings = new List<(string Ns, string? Alias, bool Static)>();
        var ivtItems = new List<(string Name, string Key)>();               // <InternalsVisibleTo Include>
        var ivtAttrs = new List<(string Type, string? Param)>();            // <AssemblyAttribute Include>
        bool appHost = p.Sdks.Any(s => s.Equals("Aspire.AppHost.Sdk", StringComparison.OrdinalIgnoreCase));
        foreach (var phase in new[] { "props", "defaults", "project", "targets" })
        {
            if (phase == "defaults")
            {
                if (p.DefaultCompile)
                {
                    foreach (var f in c.Cs.Where(f => claimDir(f) == p.Dir && !BinObj(f, p.Dir)))
                    {
                        if (set.Add(f))
                        {
                            compile.Add(f);
                        }
                    }
                }
                if (p.ImplicitUsings)
                {
                    usings.AddRange(SdkUsings(p).Select(u => (u, (string?)null, false)));
                }
                continue;
            }
            foreach (var op in p.Items.Where(x => x.Phase == phase))
            {
                switch (op.Type)
                {
                    case "Compile":
                    {
                        var hit = MatchCs(c, p, op.Spec, $"{op.From}: <Compile {op.Op}>");
                        if (op.Exclude != null)
                        {
                            var ex = new HashSet<string>(MatchCs(c, p, op.Exclude, $"{op.From}: <Compile Exclude>"), StringComparer.Ordinal);
                            hit = hit.Where(f => !ex.Contains(f)).ToList();
                        }
                        foreach (var f in hit)
                        {
                            if (op.Op == "Include")
                            {
                                if (set.Add(f))
                                {
                                    compile.Add(f);
                                }
                            }
                            else if (set.Remove(f))
                            {
                                compile.Remove(f);
                                removed.Add(f);
                            }
                        }
                        break;
                    }
                    case "ProjectReference":
                        foreach (var piece in op.Spec.Split(';', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
                        {
                            string? r = P.Resolve(piece, p.Dir);
                            string? t = r == null ? null : c.Find(r);
                            if (op.Op == "Remove")
                            {
                                if (t != null)
                                {
                                    p.DirectRefs.Remove(t);
                                }
                                continue;
                            }
                            string? oit = op.Meta.GetValueOrDefault("OutputItemType");
                            string? roa = op.Meta.GetValueOrDefault("ReferenceOutputAssembly");
                            string? res = op.Meta.GetValueOrDefault("IsAspireProjectResource");
                            string? why = t == null || !t.EndsWith(".csproj", StringComparison.OrdinalIgnoreCase) ? "not_a_tracked_csproj"
                                        : string.Equals(oit, "Analyzer", StringComparison.OrdinalIgnoreCase) ? "output_item_type_analyzer"
                                        : string.Equals(roa, "false", StringComparison.OrdinalIgnoreCase) ? "reference_output_assembly_false"
                                        : appHost && !string.Equals(res, "false", StringComparison.OrdinalIgnoreCase) ? "aspire_project_resource"
                                        : t == p.Rel ? "self" : null;
                            if (why != null)
                            {
                                p.SkippedRefs.Add(new JsonArray(t ?? piece.Replace('\\', '/'), why, op.From));
                            }
                            else if (!p.DirectRefs.Contains(t!))
                            {
                                p.DirectRefs.Add(t!);
                            }
                        }
                        break;
                    case "Using":
                        foreach (var piece in op.Spec.Split(';', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
                        {
                            if (op.Op == "Remove")
                            {
                                usings.RemoveAll(x => x.Ns.Equals(piece, StringComparison.OrdinalIgnoreCase));
                                continue;
                            }
                            string? alias = op.Meta.GetValueOrDefault("Alias");
                            bool st = string.Equals(op.Meta.GetValueOrDefault("Static"), "true", StringComparison.OrdinalIgnoreCase);
                            usings.Add((piece, string.IsNullOrEmpty(alias) ? null : alias, st));
                        }
                        break;
                    case "InternalsVisibleTo":
                        foreach (var piece in op.Spec.Split(';', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
                        {
                            if (op.Op == "Remove")
                            {
                                ivtItems.RemoveAll(x => x.Name.Equals(piece, StringComparison.OrdinalIgnoreCase));
                                continue;
                            }
                            // GetAssemblyAttributes: a PublicKey metadata is the Key
                            string pk = op.Meta.GetValueOrDefault("PublicKey") ?? "";
                            ivtItems.Add((piece, pk.Length > 0 ? pk : op.Meta.GetValueOrDefault("Key") ?? ""));
                        }
                        break;
                    case "AssemblyAttribute":
                        foreach (var piece in op.Spec.Split(';', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
                        {
                            if (op.Op == "Remove")
                            {
                                ivtAttrs.RemoveAll(x => x.Type.Equals(piece, StringComparison.OrdinalIgnoreCase));
                                continue;
                            }
                            if (IvtAttributeNames.Contains(piece, StringComparer.Ordinal))
                            {
                                ivtAttrs.Add((piece, op.Meta.GetValueOrDefault("_Parameter1")));
                            }
                        }
                        break;
                }
            }
        }
        // the MAUI SDK's _MauiRemovePlatformCompileItems (a target: after evaluation, so after every item above)
        if (p.UseMaui)
        {
            string prefix = (p.Dir.Length == 0 ? "" : p.Dir + "/") + "Platforms/";
            string? keep = MauiFolders.TryGetValue(MsEval.TargetPlatform(p.FirstTf), out var folder) ? prefix + folder + "/" : null;
            foreach (var f in compile.Where(f => f.StartsWith(prefix, StringComparison.OrdinalIgnoreCase) &&
                                                 (keep == null || !f.StartsWith(keep, StringComparison.OrdinalIgnoreCase))).ToList())
            {
                compile.Remove(f);
                set.Remove(f);
                p.MauiRemoved.Add(f);
            }
            p.MauiRemoved.Sort(StringComparer.Ordinal);
        }
        // the InternalsVisibleTo attributes Microsoft.NET.GenerateAssemblyInfo.targets writes
        if (p.GenerateAssemblyInfo)
        {
            foreach (var (type, param) in ivtAttrs)
            {
                if (string.IsNullOrEmpty(param))
                {
                    p.Notes.Add($"<AssemblyAttribute Include=\"{type}\"> without _Parameter1: not written");
                    continue;
                }
                p.Ivt.Add(param);
            }
            if (p.GenerateIvtAttributes)
            {
                foreach (var (name, key) in ivtItems)
                {
                    p.Ivt.Add(key.Length > 0 ? $"{name}, PublicKey={key}"
                              : p.PublicKey.Length > 0 ? $"{name}, PublicKey={p.PublicKey}" : name);
                }
            }
        }
        var ivt = p.Ivt.Distinct(StringComparer.Ordinal).OrderBy(x => x, StringComparer.Ordinal).ToList();
        p.Ivt.Clear();
        p.Ivt.AddRange(ivt);
        compile.Sort(StringComparer.Ordinal);
        p.Files.AddRange(compile);
        p.Links.AddRange(compile.Where(f => claimDir(f) != p.Dir));
        p.Removed.AddRange(removed.Where(f => !set.Contains(f)));
        p.UsingLines.AddRange(usings.Select(x => x.Alias != null ? $"global using {x.Alias} = global::{x.Ns};"
                                                 : x.Static ? $"global using static global::{x.Ns};"
                                                 : $"global using global::{x.Ns};")
                                    .Distinct(StringComparer.Ordinal).OrderBy(x => x, StringComparer.Ordinal));
    }

    /// <summary>Every project in dependency order, ties broken by byte order of the paths (PREREG_CS AC3): the
    /// lexicographically smallest topological order of the project-reference graph, i.e. at each step the
    /// byte-smallest project whose references are all placed. In a cycle (no such project) the byte-smallest of the
    /// rest is placed and its unplaced references are noted as skipped back edges.</summary>
    private static List<ProjectInfo> TopoOrder(List<ProjectInfo> projects, Dictionary<string, ProjectInfo> byRel)
    {
        var placed = new HashSet<string>(StringComparer.Ordinal);
        var left = new SortedSet<string>(projects.Select(p => p.Rel), StringComparer.Ordinal);
        var order = new List<ProjectInfo>();
        while (left.Count > 0)
        {
            string? next = left.FirstOrDefault(r => byRel[r].DirectRefs.All(x => x == r || placed.Contains(x)));
            if (next == null)
            {
                next = left.Min!;
                foreach (var x in byRel[next].DirectRefs.Where(x => x != next && !placed.Contains(x))
                                                        .OrderBy(x => x, StringComparer.Ordinal))
                {
                    byRel[next].Notes.Add($"project reference cycle: {next} -> {x} (skipped)");
                }
            }
            left.Remove(next);
            placed.Add(next);
            order.Add(byRel[next]);
        }
        return order;
    }

    private static List<string> Closure(ProjectInfo p, Dictionary<string, ProjectInfo> byRel, List<ProjectInfo> order)
    {
        var set = new HashSet<string>(StringComparer.Ordinal);
        var stack = new Stack<string>(p.DirectRefs);
        while (stack.Count > 0)
        {
            string r = stack.Pop();
            if (r == p.Rel || !set.Add(r))
            {
                continue;
            }
            if (p.TransitiveRefs)
            {
                foreach (var x in byRel[r].DirectRefs)
                {
                    stack.Push(x);
                }
            }
        }
        // a reference that comes later in the build order than the project itself is a cycle's back edge
        int me = order.IndexOf(p);
        return order.Where((x, i) => set.Contains(x.Rel) && i < me).Select(x => x.Rel).ToList();
    }

    // ================================================================================================ call sites
    private static (int, int, string, int, int, int, int, JsonObject) Safe(
        Func<(int, int, string, int, int, int, int, JsonObject)> f, SyntaxNode node, Unit u, Ctx ctx, ref int errors)
    {
        try
        {
            return f();
        }
        catch (Exception exc)
        {
            errors++;
            string file = ctx.Rel[node.SyntaxTree];
            var lf = ctx.Lines[file];
            string msg = exc.GetType().Name + ": " + exc.Message;
            var row = new JsonObject
            {
                ["file"] = file,
                ["line"] = lf.Line(node.SpanStart),
                ["col"] = lf.Col(node.SpanStart),
                ["name"] = null,
                ["name_line"] = null,
                ["name_col"] = null,
                ["site_kind"] = node is InvocationExpressionSyntax ? "invocation" : "object_creation",
                ["form"] = "other",
                ["project"] = u.Id,
                ["defstack"] = new JsonArray(),
                ["resolution"] = "none",
                ["n_candidates"] = 0,
                ["candidate_reason"] = null,
                ["receiver_type"] = null,
                ["receiver_type_kind"] = null,
                ["target"] = null,
                ["dispatch_rule"] = null,
                ["dispatch_none"] = "error",
                ["dispatch_raw"] = new JsonArray(),
                ["dispatch_ac1_added"] = new JsonArray(),
                ["receiver_reading"] = null,
                ["internals_visible_to"] = null,
                ["error"] = msg.Length > 200 ? msg[..200] : msg,
            };
            return (lf.Line(node.SpanStart), lf.Col(node.SpanStart), "", 0, 0, node is InvocationExpressionSyntax ? 0 : 1,
                    node.Span.End, row);
        }
    }

    private static bool IsNameOf(InvocationExpressionSyntax inv, SemanticModel model) =>
        inv.Expression is IdentifierNameSyntax { Identifier.ValueText: "nameof" } &&
        model.GetSymbolInfo(inv).Symbol == null && model.GetOperation(inv) is INameOfOperation;

    private static string FormOf(ExpressionSyntax r) => r switch
    {
        ThisExpressionSyntax => "this",
        BaseExpressionSyntax => "super",
        IdentifierNameSyntax or GenericNameSyntax or PredefinedTypeSyntax => "name_attr",
        _ => "recv",
    };

    private static (int, int, string, int, int, int, int, JsonObject) Site(ExpressionSyntax site, SemanticModel model,
                                                                            Unit u, Ctx ctx)
    {
        string file = ctx.Rel[site.SyntaxTree];
        var lf = ctx.Lines[file];
        bool creation = site is BaseObjectCreationExpressionSyntax;
        var si = model.GetSymbolInfo(site);
        string form;
        string? name;
        SyntaxToken? nameTok;
        ExpressionSyntax? receiver = null;          // the receiver expression, when one is written
        bool implicitReceiver = false, conditional = false;
        ITypeSymbol? createdType = null;
        if (creation)
        {
            var oc = (BaseObjectCreationExpressionSyntax)site;
            form = "ctor";
            nameTok = oc is ObjectCreationExpressionSyntax o ? RightmostName(o.Type) : oc.NewKeyword;
            createdType = model.GetTypeInfo(oc).Type;
            name = createdType is { } ct && ct.Name.Length > 0 ? ct.Name
                 : oc is ObjectCreationExpressionSyntax o2 ? RightmostName(o2.Type)?.ValueText : null;
        }
        else
        {
            var inv = (InvocationExpressionSyntax)site;
            var e = inv.Expression;
            SimpleNameSyntax? nameNode = e switch
            {
                SimpleNameSyntax s => s,
                MemberAccessExpressionSyntax ma => ma.Name,
                MemberBindingExpressionSyntax mb => mb.Name,
                _ => null,
            };
            nameTok = nameNode?.Identifier;
            name = nameNode?.Identifier.ValueText;
            if (e is SimpleNameSyntax)
            {
                form = "bare";
                implicitReceiver = true;
            }
            else if (e is MemberAccessExpressionSyntax ma && ma.IsKind(SyntaxKind.SimpleMemberAccessExpression))
            {
                receiver = ma.Expression;
                form = FormOf(receiver);
            }
            else if (e is MemberBindingExpressionSyntax mb && ConditionalOf(mb) is { } ca)
            {
                receiver = ca.Expression;
                form = FormOf(receiver);
                conditional = true;
            }
            else
            {
                form = "other";
                name = nameNode?.Identifier.ValueText;
            }
        }

        var bound = si.Symbol;
        // the receiver's static type
        ITypeSymbol? recvType = null;
        if (receiver != null)
        {
            recvType = model.GetTypeInfo(receiver).Type;
            if (conditional && recvType is INamedTypeSymbol { OriginalDefinition.SpecialType: SpecialType.System_Nullable_T } nt)
            {
                recvType = nt.TypeArguments[0];
            }
        }
        else if (implicitReceiver && bound is IMethodSymbol { IsStatic: false, MethodKind: not MethodKind.LocalFunction })
        {
            recvType = EnclosingType(model, site.SpanStart);
        }

        var row = new JsonObject
        {
            ["file"] = file,
            ["line"] = lf.Line(site.SpanStart),
            ["col"] = lf.Col(site.SpanStart),
            ["name"] = name,
            ["name_line"] = nameTok != null ? lf.Line(nameTok.Value.SpanStart) : null,
            ["name_col"] = nameTok != null ? lf.Col(nameTok.Value.SpanStart) : null,
            ["site_kind"] = creation ? "object_creation" : "invocation",
            ["form"] = form,
            ["project"] = u.Id,
            ["defstack"] = DefStack(site, lf, model),
            ["resolution"] = bound != null ? "symbol" : si.CandidateSymbols.Length > 0 ? "candidates" : "none",
            ["n_candidates"] = bound != null ? 0 : si.CandidateSymbols.Length,
            ["candidate_reason"] = bound == null && si.CandidateReason != CandidateReason.None ? si.CandidateReason.ToString() : null,
            ["receiver_type"] = recvType?.ToDisplayString(TypeFmt),
            ["receiver_type_kind"] = recvType == null ? null : recvType.TypeKind == TypeKind.TypeParameter ? "type_parameter"
                                     : recvType.TypeKind.ToString().ToLowerInvariant(),
        };

        // ---- the static binding --------------------------------------------------------------------------------
        JsonObject? target = null;
        IMethodSymbol? sm = null;
        if (bound != null)
        {
            if (creation)
            {
                var type = (bound as IMethodSymbol)?.MethodKind == MethodKind.Constructor ? bound.ContainingType
                         : createdType as INamedTypeSymbol;
                target = type == null ? TargetOther(bound) : TypeTarget(type, ctx);
            }
            else if (bound is IMethodSymbol bm)
            {
                sm = bm;
                target = MethodTarget(bm, ctx);
            }
            else
            {
                target = TargetOther(bound);
            }
        }
        row["target"] = target;

        // ---- dispatch --------------------------------------------------------------------------------------------
        string? rule = null, none, reading = null;
        JsonArray raw = new(), added = new();
        if (bound == null)
        {
            none = "unresolved";
        }
        else if (creation)
        {
            none = "object_creation";
        }
        else if (sm == null)
        {
            none = "not_a_method";
        }
        else
        {
            bool baseCall = receiver is BaseExpressionSyntax;
            JsonArray pre;
            (rule, none, raw, pre, reading) = Dispatch(sm, recvType, baseCall, u, ctx);
            if (rule == "interface")                       // AC1 amends the interface rule only
            {
                var had = pre.Select(x => ((string)x!["file"]!, (int)x!["line"]!)).ToHashSet();
                foreach (var x in raw.Where(x => !had.Contains(((string)x!["file"]!, (int)x!["line"]!))))
                {
                    added.Add(x!.DeepClone());
                }
            }
        }
        row["dispatch_rule"] = rule;
        row["dispatch_none"] = none;
        row["dispatch_raw"] = raw;
        // NOT the truth, never in the truth rows: the interface-rule targets AC1 adds beyond the members
        // FindImplementationForInterfaceMember returns (the registered text before AC1); counted in the header
        row["dispatch_ac1_added"] = added;
        // the override rule's reading of a type-parameter receiver (AC2): class_constraint, inherited_class_constraint,
        // object, value_type; null otherwise
        row["receiver_reading"] = reading;
        row["internals_visible_to"] = bound == null ? null : FriendAccess(bound, model, site, u, ctx);
        int nl = nameTok != null ? lf.Line(nameTok.Value.SpanStart) : 0;
        int nc = nameTok != null ? lf.Col(nameTok.Value.SpanStart) : 0;
        return (lf.Line(site.SpanStart), lf.Col(site.SpanStart), name ?? "", nl, nc, creation ? 1 : 0, site.Span.End, row);
    }

    /// <summary>Whether binding this site needed friend access (AC3's generated InternalsVisibleTo): the bound member,
    /// or a type containing it, is internal / private protected (or protected internal, reached from a type that does
    /// not derive from its containing type) and declared in ANOTHER corpus project's assembly. "generated" when an
    /// MSBuild-generated InternalsVisibleTo attribute of that assembly names the caller's assembly, "source" when not
    /// (a source attribute opened it), null when no friend access was needed.</summary>
    private static string? FriendAccess(ISymbol bound, SemanticModel model, SyntaxNode site, Unit u, Ctx ctx)
    {
        ISymbol s = bound is IMethodSymbol m ? (m.ReducedFrom ?? m).OriginalDefinition : bound.OriginalDefinition;
        string? asm = s.ContainingAssembly?.Name;
        string me = u.Compilation.AssemblyName ?? "";
        if (asm == null || asm == me || !ctx.GeneratedFriends.TryGetValue(asm, out var friends))
        {
            return null;                                   // the caller's own assembly, or not a corpus project
        }
        INamedTypeSymbol? from = null;
        bool needs = false;
        for (ISymbol? x = s; x != null && x is not INamespaceSymbol; x = x.ContainingType)
        {
            switch (x.DeclaredAccessibility)
            {
                case Accessibility.Internal:
                case Accessibility.ProtectedAndInternal:
                    needs = true;
                    break;
                case Accessibility.ProtectedOrInternal when x.ContainingType is { } owner:
                    from ??= EnclosingType(model, site.SpanStart);
                    needs |= from == null || !K.BaseChain(from).Any(b => K.Type(b) == K.Type(owner));
                    break;
            }
        }
        return !needs ? null : friends.Contains(me) ? "generated" : "source";
    }

    private static SyntaxToken? RightmostName(TypeSyntax t) => t switch
    {
        SimpleNameSyntax s => s.Identifier,
        QualifiedNameSyntax q => q.Right.Identifier,
        AliasQualifiedNameSyntax a => a.Name.Identifier,
        NullableTypeSyntax n => RightmostName(n.ElementType),
        _ => null,
    };

    /// <summary>The conditional access whose `?.` reaches this member binding: the innermost ancestor whose
    /// WhenNotNull holds it.</summary>
    private static ConditionalAccessExpressionSyntax? ConditionalOf(SyntaxNode n)
    {
        for (var p = n.Parent; p != null; p = p.Parent)
        {
            if (p is ConditionalAccessExpressionSyntax ca && ca.WhenNotNull.Span.Contains(n.Span))
            {
                return ca;
            }
        }
        return null;
    }

    private static INamedTypeSymbol? EnclosingType(SemanticModel model, int pos)
    {
        ISymbol? s = model.GetEnclosingSymbol(pos);
        while (s != null && s is not INamedTypeSymbol)
        {
            s = s.ContainingSymbol;
        }
        return s as INamedTypeSymbol;
    }

    // ================================================================================================= defstack
    private static bool IsMemberEntry(SyntaxNode n) => n is MethodDeclarationSyntax or ConstructorDeclarationSyntax
        or DestructorDeclarationSyntax or OperatorDeclarationSyntax or ConversionOperatorDeclarationSyntax
        or PropertyDeclarationSyntax or IndexerDeclarationSyntax or EventDeclarationSyntax;

    private static JsonArray DefStack(SyntaxNode site, LfLines lf, SemanticModel model)
    {
        var locals = new List<LocalFunctionStatementSyntax>();
        SyntaxNode? member = null;
        for (var p = site.Parent; p != null; p = p.Parent)
        {
            if (p is LocalFunctionStatementSyntax l)
            {
                locals.Add(l);
            }
            else if (IsMemberEntry(p))
            {
                member = p;
                break;
            }
            else if (p is MemberDeclarationSyntax)
            {
                break;                                     // a field / event field / type / enum member / global statement
            }
        }
        locals.Reverse();
        var ds = new JsonArray();
        string prefix = TypeChain(member ?? site);
        if (member != null)
        {
            string q = Join(prefix, MemberName(member));
            var partial = member is MethodDeclarationSyntax ? model.GetDeclaredSymbol(member) as IMethodSymbol : null;
            ds.Add(new JsonArray(q, lf.Line(member.SpanStart),
                                 Ints(AltLines(member, lf, PartialDefinitionLine(partial, member.SyntaxTree, lf)))));
            prefix = q;
        }
        foreach (var l in locals)
        {
            prefix = Join(prefix, l.Identifier.ValueText);
            ds.Add(new JsonArray(prefix, lf.Line(l.SpanStart), Ints(AltLines(l, lf))));
        }
        return ds;
    }

    private static string Join(string a, string b) => a.Length == 0 ? b : a + "." + b;

    private static JsonArray Ints(IEnumerable<int> xs) => new(xs.Select(x => (JsonNode)JsonValue.Create(x)).ToArray());

    private static string MemberName(SyntaxNode m) => m switch
    {
        MethodDeclarationSyntax x => x.Identifier.ValueText,
        ConstructorDeclarationSyntax x => x.Identifier.ValueText,
        DestructorDeclarationSyntax x => "~" + x.Identifier.ValueText,
        OperatorDeclarationSyntax x => "operator " + x.OperatorToken.ValueText,
        ConversionOperatorDeclarationSyntax x => "operator " + x.Type.ToString(),
        PropertyDeclarationSyntax x => x.Identifier.ValueText,
        IndexerDeclarationSyntax => "this",
        EventDeclarationSyntax x => x.Identifier.ValueText,
        _ => "?",
    };

    /// <summary>The type declarations enclosing a node, outermost first, joined by '.' (no namespace); a type
    /// declaration itself is not part of its own chain.</summary>
    private static string TypeChain(SyntaxNode n)
    {
        var names = n.Ancestors().OfType<BaseTypeDeclarationSyntax>().Select(t => t.Identifier.ValueText).Reverse();
        return string.Join(".", names);
    }

    private static SyntaxToken? NameToken(SyntaxNode n) => n switch
    {
        MethodDeclarationSyntax x => x.Identifier,
        ConstructorDeclarationSyntax x => x.Identifier,
        DestructorDeclarationSyntax x => x.Identifier,
        OperatorDeclarationSyntax x => x.OperatorToken,
        ConversionOperatorDeclarationSyntax x => x.OperatorKeyword,
        PropertyDeclarationSyntax x => x.Identifier,
        IndexerDeclarationSyntax x => x.ThisKeyword,
        EventDeclarationSyntax x => x.Identifier,
        BaseTypeDeclarationSyntax x => x.Identifier,
        DelegateDeclarationSyntax x => x.Identifier,
        LocalFunctionStatementSyntax x => x.Identifier,
        _ => null,
    };

    private static SyntaxList<AttributeListSyntax> AttributeLists(SyntaxNode n) => n switch
    {
        MemberDeclarationSyntax m => m.AttributeLists,
        LocalFunctionStatementSyntax l => l.AttributeLists,
        _ => default,
    };

    /// <summary>AC4: the first line (counting attribute lists) of a partial method implementation part's DEFINING
    /// declaration, when that declaration is in the same tree; else null.</summary>
    private static int? PartialDefinitionLine(IMethodSymbol? m, SyntaxTree tree, LfLines lf)
    {
        if (m?.PartialDefinitionPart is not { } def)
        {
            return null;
        }
        var r = def.DeclaringSyntaxReferences.FirstOrDefault(x => x.SyntaxTree == tree);
        return r == null ? null : lf.Line(r.GetSyntax().SpanStart);
    }

    /// <summary>A declaration's alternative lines: the first line after its attribute lists, its name line, for an
    /// overloaded method or constructor the first line of the first same-named member of the same type declaration
    /// (DeepMap's engine makes one node for all overloads there) and, for a partial method's implementation part, its
    /// defining declaration's first line in the same file (AC4: the engine's only node is there).</summary>
    private static List<int> AltLines(SyntaxNode decl, LfLines lf, int? partialDefinition = null)
    {
        var outList = new List<int>();
        int start = lf.Line(decl.SpanStart);
        void Add(int x)
        {
            if (x != start && !outList.Contains(x))
            {
                outList.Add(x);
            }
        }
        var al = AttributeLists(decl);
        if (al.Count > 0)
        {
            int end = al[al.Count - 1].Span.End;
            var tok = decl.DescendantTokens().FirstOrDefault(t => t.SpanStart >= end);
            if (tok != default)
            {
                Add(lf.Line(tok.SpanStart));
            }
        }
        var nt = NameToken(decl);
        if (nt != null)
        {
            Add(lf.Line(nt.Value.SpanStart));
        }
        if (decl.Parent is TypeDeclarationSyntax td)
        {
            SyntaxNode? first = decl switch
            {
                MethodDeclarationSyntax md => td.Members.OfType<MethodDeclarationSyntax>()
                                                .FirstOrDefault(x => x.Identifier.ValueText == md.Identifier.ValueText),
                ConstructorDeclarationSyntax => td.Members.OfType<ConstructorDeclarationSyntax>().FirstOrDefault(),
                _ => null,
            };
            if (first != null && first != decl)
            {
                Add(lf.Line(first.SpanStart));
            }
        }
        if (partialDefinition is int pd)
        {
            Add(pd);
        }
        return outList;
    }

    // ================================================================================================== targets
    private static bool HasBody(SyntaxNode n) => n switch
    {
        BaseMethodDeclarationSyntax m => m.Body != null || m.ExpressionBody != null,
        LocalFunctionStatementSyntax l => l.Body != null || l.ExpressionBody != null,
        AccessorDeclarationSyntax a => a.Body != null || a.ExpressionBody != null,
        BaseTypeDeclarationSyntax => true,
        _ => false,
    };

    /// <summary>Every declaration of a symbol in a compiled corpus file: {file, line, alt_lines, body}, sorted.</summary>
    private static JsonArray Decls(ISymbol s, Ctx ctx)
    {
        var l = new List<(string File, int Line, JsonObject O)>();
        if (s.IsImplicitlyDeclared)
        {
            return new JsonArray();
        }
        foreach (var r in s.DeclaringSyntaxReferences)
        {
            if (!ctx.Rel.TryGetValue(r.SyntaxTree, out var file))
            {
                continue;
            }
            var n = r.GetSyntax();
            var lf = ctx.Lines[file];
            int line = lf.Line(n.SpanStart);
            int? pd = PartialDefinitionLine(s as IMethodSymbol, r.SyntaxTree, lf);
            l.Add((file, line, new JsonObject
            {
                ["file"] = file, ["line"] = line, ["alt_lines"] = Ints(AltLines(n, lf, pd)), ["body"] = HasBody(n),
            }));
        }
        var arr = new JsonArray();
        foreach (var x in l.OrderBy(x => x.File, StringComparer.Ordinal).ThenBy(x => x.Line))
        {
            if (!arr.Any(a => (string)a!["file"]! == x.File && (int)a!["line"]! == x.Line))
            {
                arr.Add(x.O);
            }
        }
        return arr;
    }

    private static string DeclName(IMethodSymbol m)
    {
        foreach (var r in m.DeclaringSyntaxReferences)
        {
            switch (r.GetSyntax())
            {
                case MethodDeclarationSyntax md:
                    return md.Identifier.ValueText;           // an explicit interface implementation's own name
                case LocalFunctionStatementSyntax lfs:
                    return lfs.Identifier.ValueText;
            }
        }
        int dot = m.Name.LastIndexOf('.');
        return dot >= 0 ? m.Name[(dot + 1)..] : m.Name;
    }

    private static string FullName(IMethodSymbol m, string name)
    {
        string ct = m.ContainingType?.ToDisplayString(TypeFmt) ?? "";
        if (m.MethodKind == MethodKind.LocalFunction && m.ContainingSymbol is IMethodSymbol host)
        {
            return Join(Join(ct, host.Name), name);
        }
        return Join(ct, name);
    }

    /// <summary>The method a call binds to, as its original definition.</summary>
    private static IMethodSymbol Definition(IMethodSymbol m)
    {
        var d = (m.ReducedFrom ?? m).OriginalDefinition;
        return d.PartialImplementationPart ?? d;
    }

    private static JsonObject MethodTarget(IMethodSymbol bound, Ctx ctx)
    {
        var d = Definition(bound);
        string name = DeclName(d);
        bool invoke = d.MethodKind == MethodKind.DelegateInvoke;
        return new JsonObject
        {
            ["kind"] = d.MethodKind == MethodKind.LocalFunction ? "local_function" : "method",
            ["method_kind"] = d.MethodKind.ToString(),
            ["name"] = name,
            ["full_name"] = FullName(d, name),
            ["interface"] = d.ContainingType?.TypeKind == TypeKind.Interface,
            ["static"] = d.IsStatic,
            ["virtual"] = d.IsVirtual,
            ["abstract"] = d.IsAbstract,
            ["override"] = d.IsOverride,
            ["sealed"] = d.IsSealed,
            ["extension"] = d.IsExtensionMethod,
            ["decls"] = invoke ? new JsonArray() : Decls(d, ctx),
        };
    }

    private static JsonObject TypeTarget(INamedTypeSymbol t, Ctx ctx)
    {
        var d = t.OriginalDefinition;
        return new JsonObject
        {
            ["kind"] = "type",
            ["method_kind"] = null,
            ["name"] = d.Name,
            ["full_name"] = d.ToDisplayString(TypeFmt),
            ["interface"] = d.TypeKind == TypeKind.Interface,
            ["static"] = d.IsStatic,
            ["virtual"] = false,
            ["abstract"] = d.IsAbstract,
            ["override"] = false,
            ["sealed"] = d.IsSealed,
            ["extension"] = false,
            ["decls"] = Decls(d, ctx),
        };
    }

    private static JsonObject TargetOther(ISymbol s) => new()
    {
        ["kind"] = s.Kind.ToString().ToLowerInvariant(),
        ["method_kind"] = null,
        ["name"] = s.Name,
        ["full_name"] = s.ToDisplayString(TypeFmt),
        ["interface"] = false,
        ["static"] = s.IsStatic,
        ["virtual"] = s.IsVirtual,
        ["abstract"] = s.IsAbstract,
        ["override"] = s.IsOverride,
        ["sealed"] = s.IsSealed,
        ["extension"] = false,
        ["decls"] = new JsonArray(),
    };

    // ================================================================================================= dispatch
    private static (string? Rule, string? None, JsonArray Targets, JsonArray PreAc1, string? Reading) Dispatch(
        IMethodSymbol bound, ITypeSymbol? recv, bool baseCall, Unit u, Ctx ctx)
    {
        var sd = Definition(bound);
        static (string?, string?, JsonArray, JsonArray, string?) None(string why) =>
            (null, why, new JsonArray(), new JsonArray(), null);
        if (bound.ReducedFrom != null || sd.IsExtensionMethod)
        {
            return None("extension");
        }
        if (sd.MethodKind == MethodKind.DelegateInvoke)
        {
            return None("delegate_invoke");
        }
        if (sd.MethodKind == MethodKind.LocalFunction)
        {
            return None("local_function");
        }
        if (sd.IsStatic)
        {
            return None("static");
        }
        if (baseCall)
        {
            return None("base_call");
        }
        bool iface = sd.ContainingType?.TypeKind == TypeKind.Interface;
        if (!iface)
        {
            if (sd.IsSealed)
            {
                return None("sealed");
            }
            if (!(sd.IsVirtual || sd.IsAbstract || sd.IsOverride))
            {
                return None("non_virtual");
            }
        }
        string rule = iface ? "interface" : "override";
        // AC2: the override rule reads a type-parameter receiver as its class constraint, else System.Object /
        // System.ValueType. The interface rule reads none: a type-parameter receiver reaches every implementer.
        string? reading = null;
        ITypeSymbol? asRead = recv;
        if (!iface && recv is ITypeParameterSymbol tp)
        {
            (asRead, reading) = ClassConstraint(tp, u);
        }
        // one result per (rule, S, the receiver AS THE RULE READS IT, the interface S was reached through): the key is
        // the resolved receiver, so a cached result never stands for another constraint's (whichever file came first)
        string key = rule + "\0" + K.Method(sd) + "\0" + K.Struct(asRead) + "\0" + (iface ? K.Struct(bound.ContainingType) : "");
        var got = ctx.DispatchCache.GetOrAdd(key, _ =>
        {
            string? why;
            var pre = new List<IMethodSymbol>();
            var found = iface ? InterfaceTargets(bound, sd, recv, ctx, pre, out why)
                              : OverrideTargets(sd, asRead, ctx, out why);
            return (rule, why, Entries(found, ctx), Entries(pre, ctx));
        });
        return (got.Rule, got.None, (JsonArray)got.Targets.DeepClone(), (JsonArray)got.PreAc1.DeepClone(), reading);
    }

    /// <summary>AC2: a type parameter as the override rule reads it: its class constraint (the most derived class
    /// constraint of it or of a type parameter it is constrained to, i.e. its effective base class), else
    /// System.ValueType under a struct / unmanaged constraint, else System.Object; with the reading's name.</summary>
    private static (ITypeSymbol Type, string Reading) ClassConstraint(ITypeParameterSymbol tp, Unit u)
    {
        var seen = new HashSet<ITypeParameterSymbol>(SymbolEqualityComparer.Default);
        INamedTypeSymbol? best = null;
        bool inherited = false, value = false;
        void Visit(ITypeParameterSymbol t, bool viaParameter)
        {
            if (!seen.Add(t))
            {
                return;
            }
            value |= t.HasValueTypeConstraint || t.HasUnmanagedTypeConstraint;
            foreach (var c in t.ConstraintTypes)
            {
                if (c is INamedTypeSymbol n && n.TypeKind == TypeKind.Class)
                {
                    var cur = best;
                    if (cur == null || K.BaseChain(n).Skip(1).Any(b => K.Type(b) == K.Type(cur)))
                    {
                        best = n;
                        inherited = viaParameter;
                    }
                }
                else if (c is ITypeParameterSymbol other)
                {
                    Visit(other, true);
                }
            }
        }
        Visit(tp, false);
        if (best != null)
        {
            return (best, inherited ? "inherited_class_constraint" : "class_constraint");
        }
        return value ? (u.Compilation.GetSpecialType(SpecialType.System_ValueType), "value_type")
                     : (u.Compilation.GetSpecialType(SpecialType.System_Object), "object");
    }

    private static JsonArray Entries(IEnumerable<IMethodSymbol> found, Ctx ctx)
    {
        var arr = new JsonArray();
        var seen = new HashSet<(string, int)>();
        foreach (var x in found.SelectMany(m => TargetEntries(m, ctx))
                               .OrderBy(o => (string)o["file"]!, StringComparer.Ordinal)
                               .ThenBy(o => (int)o["line"]!)
                               .ThenBy(o => (string)o["full_name"]!, StringComparer.Ordinal))
        {
            if (seen.Add(((string)x["file"]!, (int)x["line"]!)))
            {
                arr.Add(x);
            }
        }
        return arr;
    }

    /// <summary>AC1: the method an object of exact type t runs for the implementing member `impl`
    /// FindImplementationForInterfaceMember returned, when impl is a virtual, abstract or override class member: the
    /// most derived override of it on t's base chain (whose OverriddenMethod chain reaches impl's root: a `new` slot
    /// starts its own root and is skipped); null when nothing below impl's own type overrides it (impl runs).</summary>
    private static IMethodSymbol? RuntimeOverride(INamedTypeSymbol t, IMethodSymbol impl)
    {
        if (impl.ContainingType == null || impl.ContainingType.TypeKind == TypeKind.Interface || impl.IsSealed ||
            !(impl.IsVirtual || impl.IsAbstract || impl.IsOverride))
        {
            return null;
        }
        string root = K.Method(K.Root(impl));
        string owner = K.Type(impl.ContainingType);
        foreach (var b in K.BaseChain(t))
        {
            if (K.Type(b) == owner)
            {
                return null;
            }
            foreach (var m in b.OriginalDefinition.GetMembers(impl.Name).OfType<IMethodSymbol>())
            {
                if (m.IsOverride && K.Method(K.Root(m)) == root)
                {
                    return m.OriginalDefinition;
                }
            }
        }
        return null;
    }

    private static IEnumerable<JsonObject> TargetEntries(IMethodSymbol m, Ctx ctx)
    {
        var d = m.OriginalDefinition;
        d = d.PartialImplementationPart ?? d;
        string name = DeclName(d);
        foreach (var x in Decls(d, ctx))
        {
            var o = (JsonObject)x!;
            yield return new JsonObject
            {
                ["file"] = o["file"]!.DeepClone(), ["line"] = o["line"]!.DeepClone(), ["alt_lines"] = o["alt_lines"]!.DeepClone(),
                ["name"] = name, ["full_name"] = FullName(d, name),
                ["interface"] = d.ContainingType?.TypeKind == TypeKind.Interface, ["body"] = o["body"]!.DeepClone(),
            };
        }
    }

    /// <summary>The interface rule's targets (AC1: per qualifying type T, the method an object of exact type T runs);
    /// `pre` receives what the registered text before AC1 gave (FindImplementationForInterfaceMember's member), for
    /// the dispatch_ac1_added diagnostic.</summary>
    private static List<IMethodSymbol> InterfaceTargets(IMethodSymbol bound, IMethodSymbol sd, ITypeSymbol? recv, Ctx ctx,
                                                        List<IMethodSymbol> pre, out string? why)
    {
        why = null;
        var outList = new List<IMethodSymbol>();
        string kS = K.Method(sd);
        string kI = K.Type(sd.ContainingType);
        if (recv == null || recv.TypeKind == TypeKind.Error)
        {
            why = "no_receiver_type";
            return outList;
        }
        if (!ctx.Implementers.TryGetValue(kI, out var cands))
        {
            return outList;
        }
        bool typeParam = recv is ITypeParameterSymbol;
        var rViews = new List<INamedTypeSymbol>();
        if (!typeParam && recv is INamedTypeSymbol rn)
        {
            if (rn.TypeKind == TypeKind.Interface && K.Type(rn) == kI)
            {
                rViews.Add(rn);
            }
            rViews.AddRange(rn.AllInterfaces.Where(i => K.Type(i) == kI));
        }
        foreach (var (t, views) in cands)
        {
            List<INamedTypeSymbol> ask;
            if (typeParam)
            {
                ask = views;                               // every type implementing S's interface, every view
            }
            else
            {
                bool qualifies = recv.TypeKind == TypeKind.Interface
                    ? t.AllInterfaces.Any(i => K.Unify(i, recv))
                    : K.BaseChain(t).Any(b => K.Unify(b, recv));
                if (!qualifies)
                {
                    continue;
                }
                ask = rViews.Count == 0 ? views : views.Where(v => rViews.Any(rv => K.Unify(v, rv))).ToList();
            }
            foreach (var v in ask)
            {
                var member = v.GetMembers(sd.Name).OfType<IMethodSymbol>().FirstOrDefault(x => K.Method(x) == kS);
                if (member == null)
                {
                    continue;
                }
                if (t.FindImplementationForInterfaceMember(member) is not IMethodSymbol impl)
                {
                    continue;
                }
                var d = impl.OriginalDefinition;
                d = d.PartialImplementationPart ?? d;
                if (K.Method(d) != kS)
                {
                    pre.Add(d);
                }
                var run = RuntimeOverride(t, d) ?? d;        // AC1: what an object of exact type t runs
                if (K.Method(run) != kS)
                {
                    outList.Add(run);
                }
            }
        }
        return outList;
    }

    /// <summary>The override rule's targets for a receiver already read by AC2 (a type parameter replaced by its class
    /// constraint, System.ValueType or System.Object).</summary>
    private static List<IMethodSymbol> OverrideTargets(IMethodSymbol sd, ITypeSymbol? recv, Ctx ctx, out string? why)
    {
        why = null;
        var outList = new List<IMethodSymbol>();
        if (recv is not INamedTypeSymbol r || r.TypeKind == TypeKind.Error)
        {
            why = "no_receiver_type";
            return outList;
        }
        if (r.TypeKind == TypeKind.Interface)
        {
            why = "interface_receiver";
            return outList;
        }
        string kS = K.Method(sd);
        if (!ctx.Overrides.TryGetValue(K.Method(K.Root(sd)), out var cands))
        {
            return outList;
        }
        foreach (var (m, t) in cands)
        {
            if (K.Method(m) == kS)
            {
                continue;
            }
            if (K.BaseChain(t).Any(b => K.Unify(b, r)))
            {
                outList.Add(m);
            }
        }
        return outList;
    }
}
