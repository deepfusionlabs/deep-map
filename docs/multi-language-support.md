# DeepMap: languages supported today

*Which languages DeepMap supports, and what each tier means.*

DeepMap extracts code with a multi-language **tree-sitter AST engine** (the upstream `graphify`
package, MIT, used unchanged and installed by both installers). It runs the engine **offline**
(`graphify update --no-cluster`: **no LLM, no clustering, no network**) and maps the result into
DeepMap's typed graph schema (`:Function/:Method/:Class/:Module`). Zero-egress is by construction:
the engine is installed **bare**, without its optional LLM-provider extras, so a default install
ships no LLM SDK at all (verified with a socket tripwire across 8 languages).

## With their own end-to-end test suites (extraction on 8; search proven on 4)

DeepMap's typed-graph **extraction** has its own end-to-end test suite on all eight below, each with committed,
automated regression gates (`tests/graphify_multilang_gates.py`, built from real captured extractor
output). Its **3-arm hybrid retrieval** is additionally proven end-to-end on the **four** marked
"Retrieval-proven", demonstrated live in one mixed-language graph (a single semantic search returning
relevant symbols across Python, TypeScript, Go, and Rust at once). JavaScript, Java, Ruby, and C# have
extraction gates but not yet a retrieval proof. The table below is the source of truth:

| Language | Extensions | Gated | Retrieval-proven |
|---|---|---|---|
| Python | `.py` | ✅ | ✅ |
| JavaScript | `.js .jsx .mjs .cjs` | ✅ | — |
| TypeScript | `.ts .tsx` | ✅ | ✅ |
| Go | `.go` | ✅ | ✅ |
| Rust | `.rs` | ✅ | ✅ |
| Java | `.java` | ✅ | — |
| Ruby | `.rb` | ✅ | — |
| C# | `.cs` | ✅ | — |

The gates lock, per language: class → `:Class`, members → `:Method` (`Class.method` names), free
functions → `:Function`, files → `:Module`, the intra-service call chain resolving as `:CALLS`, and
**no junk nodes** (import/destructuring artifacts and referenced-but-not-defined external types are
filtered out).

**Calls on objects (opt-in: Python, TypeScript, JavaScript).** By default a call through an object
(`order.save()`, `this.repo.find()`) is linked by name only, or not at all. Setting
`DEEPMAP_TYPED_CALLS=python,typescript,javascript` turns on typed-receiver call linking
(`build/typed_calls.py`): it reads the receiver's declared or constructed class from the source and links the call
to that exact method. It stays off by default: measured on 10 corpora against pre-registered criteria, its edges
checked out against jedi and the TypeScript checker, but it failed other criteria (Python: the hop-1 spot symbols,
search and the frozen ordering check; TypeScript / JavaScript: the frozen ordering check)
([`benchmark/call_linking/DECISION.md`](../benchmark/call_linking/DECISION.md); limits in
[`known-limitations.md`](known-limitations.md)). Go, Java, C#, Rust and Ruby have no reader for it yet.

**Pascal/Delphi** (`.pas .pp .dpr .dpk .lpr .inc`) is gated to the same bar by its own suite,
`tests/delphi_pascal_gates.py`, also built from a real captured extractor graph. It is listed separately
from the 8 above, which are the languages `tests/graphify_multilang_gates.py` covers. The engine's parser
dispatch is **case-sensitive**, so on its own it extracts **nothing** from an uppercase `UNIT1.PAS` /
`MAIN.DPR` (common in legacy Windows Delphi trees). DeepMap aliases odd-case suffixes onto the lowercase
extractor just before the engine runs, so such trees index normally with no renaming and no user action
(`build/graphify_case_shim.py`; measured 0 → 8 nodes on a real sample; opt out with
`DEEPMAP_CASE_INSENSITIVE_EXT=0`).

## Supported by the same engine (25 grammars in the base install)

The bare engine install ships tree-sitter grammars for **25 languages**. Beyond the 8 above, these
parse through the identical pipeline but are **not yet individually gated by DeepMap** (treat as
supported-but-unverified), except Pascal/Delphi, which has its own gate suite (above) but is not counted
in the 8:

C, C++, PHP, Kotlin, Scala, Swift, Lua, Elixir, Objective-C, Julia, Bash, PowerShell, Groovy, Zig,
Fortran, Verilog and Pascal/Delphi, plus JSON (data, not a programming language).

> **How this stays true.** DeepMap keeps its own allowlist of source extensions, `SOURCE_CODE_EXTS`
> (`build/graphify_extract.py`), and it covers the engine's full parser table. Two drift locks in
> `tests/delphi_pascal_gates.py` turn a gap into a test failure: every extension the engine parses must be
> either allowed or in an explicit `EXCLUDED_BY_DESIGN_EXTS` set, and every extension DeepMap extracts must
> be countable by the setup-verdict census. Without them, a language the engine parses could be silently
> discarded, leaving a project written in it with an empty graph. Data, markup and manifest formats (`.md`,
> `.json`, `.tf`, `.sln` / `.csproj`, Delphi `.dfm` / `.lfm`) are excluded on purpose; that exclusion is
> what the allowlist is for.

Three more (SQL, HCL/Terraform, DreamMaker) are available only via the engine's optional extras and
are **not** part of DeepMap's default install.

## In short

DeepMap parses code with a tree-sitter engine installed with grammars for **25 programming
languages**. Its typed-graph extraction has its own end-to-end test suites, with committed automated gates, on
**8** of them (Python, JavaScript, TypeScript, Go, Rust, Java, Ruby and C#), and its hybrid search is
proven end-to-end on **4** (Python, TypeScript, Go and Rust). Extraction runs offline (no LLM, no
network). The rest parse through the same engine but aren't individually gated yet.

## Framework/DSL awareness (DeepMap's own recognizers, not the engine)

The tree-sitter engine gives every language *structure* (classes/methods/calls). Framework and DSL
*relationships* come from DeepMap's own per-language recognizers (`build/language_recognizers.py`
registry; offline, no LLM, regex/AST heuristics, confidence-graded):

- **Python** (`build/framework_edges.py`):
  Flask/FastAPI routes, FastAPI DI, lifecycle hooks, Celery tasks, Django/blinker signals (`@receiver`,
  `sig.connect(h)`, and event-emitter `ee.on(evt, h)`; a variable or call-result handler, and a module's
  `connect()` such as `sqlite3.connect(path)`, never become edges), inheritance, reflection, and:
  **Django URLconfs**: every `path()` / `re_path()` / `url()` entry of `urlpatterns` (and the lists it
  includes or concatenates; `+=`, `append`, `extend`, `i18n_patterns`) → `ROUTES_TO` from the URLconf module to
  the view: a function view, or a class-based view's class (`OrderView.as_view()`), through `login_required(...)`
  -style decorators and `detail = DetailView.as_view()` aliases. `include()` composes the path and the url
  namespace across files (`include('polls.urls')`, an imported urls module or pattern list,
  `(patterns, app_name)` pairs, `namespace=`, inline lists; `app_name`), recorded as the edge's `path` /
  `paths` and `url_name` / `url_names` (`polls:detail`). A view defined in the URLconf itself is EXTRACTED; an
  imported one binds in the module that defines it (followed through re-exports, star imports and aliases) and
  is INFERRED (never a same-named function in another app), and one imported from outside the tree (Django's
  own `TemplateView`, `LoginView`) binds nothing. The route functions count only when imported from
  `django.urls` / `django.conf.urls` (a pathlib or `os.path` `path` never does); `admin.site.urls`,
  `router.urls`, lambdas, factory calls and variables (a `for` / `with` target) name no view, and a route whose
  own pattern is not a literal records no `path`. **ORM relations**: SQLAlchemy classic
  (`relationship()`, `Column(ForeignKey(...))`) and **2.0 typed models** (`x: Mapped[list["Item"]] =
  relationship(...)` → `HAS_MANY`, `Mapped["Customer"]` / `Optional[...]` / `"Customer | None"` →
  `REFERENCES`, `WriteOnlyMapped`, `mapped_column(ForeignKey(...))`, `backref=`; `Mapped` from SQLAlchemy or
  re-exported by a project module), and Django `ForeignKey` / `OneToOneField` / `ManyToManyField`. A model the
  declaring file imports binds in the module its import names, so a relation to `Customer` never fans out to a
  same-named schema class; a Django `'app_label.Model'` binds in that app's `models` module, so
  `'contenttypes.ContentType'` or `'auth.Group'` (Django's own apps) never binds a project class of that name; a
  `ForeignKey("orders.id")` names the class whose `__tablename__` is `orders`, and `ForeignKey(models.User.id)`
  names `User`. An in-tree module whose definition cannot be followed (a name bound twice, a PEP 562
  `__getattr__`) falls back to the name rule, as does every import if the post-pass fails. **Cascades
  (`CASCADES`)**, traversed by `deepmap_impact`: SQLAlchemy `relationship(cascade="all, delete-orphan")` (and
  a `backref(..., cascade=...)`), `ForeignKey(..., ondelete="CASCADE" | "SET NULL")`, and Django
  `on_delete=CASCADE` / `SET_NULL`: a foreign key's principal is the model it references, so
  `deepmap_impact("Author")` lists the `Book` whose `ForeignKey(Author, on_delete=models.CASCADE)` it holds.
- **Ruby/Rails** (`build/ruby_rails_edges.py`): ActiveRecord associations
  (`HAS_MANY`/`REFERENCES`), **`dependent:` cascades (`CASCADES`)**, traversed by
  `deepmap_impact` (so "what does destroying Order cascade to?" is answerable), callbacks
  (`HOOKS`), inheritance, and **`config/routes.rb` routes → `ROUTES_TO`** (verb routes,
  `resources` RESTful expansion, `namespace`/`scope` nesting) resolving cross-file to the
  `FooController.action` handler, plus `:Method` nodes for `scope`/`namespace`/lambda route blocks.
  Heuristic, not a Ruby parser; limits in `docs/known-limitations.md`.
- **C#/.NET** (`build/csharp_edges.py`): EF Core relationships: `DbSet<T>` +
  navigation properties + the fluent API (`HasMany`/`WithOne`/`HasForeignKey`, incl.
  `IEntityTypeConfiguration<T>` config classes **and multi-statement `Entity<X>(e => { … })` lambda
  blocks**) → `HAS_MANY`/`REFERENCES`, **explicit `OnDelete(...)` cascades (`CASCADES`)**, traversed
  by `deepmap_impact` (so "what does deleting Order cascade to?" is answerable on EF codebases),
  ASP.NET Core attribute routes **and minimal-API endpoints (`app.MapGet/MapPost/…`)** →
  `ROUTES_TO`, DI from both registrations (`AddScoped`/`AddSingleton`/`AddTransient`) **and
  constructor / primary-constructor injection** → `DEPENDS_ON`, inheritance/interface
  implementation. Heuristic, not a Roslyn parser; limits in `docs/known-limitations.md`.
- **Java** (`build/java_edges.py`): Spring MVC routes (`@GetMapping`/`@PostMapping`/…/
  `@RequestMapping` with a composed class-level prefix) → `ROUTES_TO`; JPA/Hibernate relations
  (`@OneToMany`/`@ManyToMany` → `HAS_MANY`, `@ManyToOne`/`@OneToOne` → `REFERENCES`), **`cascade =
  CascadeType.REMOVE|ALL` / `orphanRemoval = true` cascades (`CASCADES`)** traversed by
  `deepmap_impact`; `extends`/`implements` → `INHERITS`/`IMPLEMENTS`; dependency injection from
  constructor params, `@Autowired` fields, and Lombok `@RequiredArgsConstructor`/`@AllArgsConstructor`
  final fields → `DEPENDS_ON`; `@Scheduled` → `SCHEDULED_AS`, `@EventListener` → `TRIGGERS`.
  Heuristic, not a javac parser; limits in `docs/known-limitations.md`.
- **Go** (`build/go_edges.py`): HTTP routes from Gin/Echo/Fiber/Chi routers
  (`r.GET/POST/…`) and `http.HandleFunc` → `ROUTES_TO`; GORM associations (slice field → `HAS_MANY`,
  struct field → `REFERENCES`), **`gorm:"constraint:OnDelete:CASCADE"` cascades (`CASCADES`)**
  traversed by `deepmap_impact`, their direction following the foreign key: a has-many slice or
  has-one field cascades owner → element, while a **belongs-to** field (`User`'s `Company Company`
  beside its `CompanyID`) makes the referenced type the principal, so `deepmap_impact("Company")`
  lists `User`. A field is belongs-to when its key sits on the declaring struct: `CompanyID` as
  above, the key a `foreignKey:` names (or `references:` implies), one renamed with `column:`, or one
  held by an embedded struct or `gorm.Model`, each looked up the way GORM does, as far as the declaring
  file shows. That applies to same-package (unqualified) types only, the principal resolved in the
  dependent's own package; a package-qualified belongs-to (`Org models.Org`) emits no cascade (its
  `REFERENCES` edge remains); struct **embedding → `INHERITS`**; `NewXxx(dep …) *Xxx` constructor
  functions → `DEPENDS_ON`. Go interface satisfaction is *implicit* and not statically knowable, so
  `IMPLEMENTS` is deliberately never emitted. Heuristic, not a `go/types` analysis.
- **TypeScript / JavaScript** (`build/typescript_edges.py`; one entry for `.ts .tsx .js .jsx
  .mjs .cjs`): **NestJS**: `@Controller` + `@Get/@Post/…` (and `@Sse`, `@Search`) routes → `ROUTES_TO`
  with the composed path (and the `app.setGlobalPrefix` of the controller's own app), constructor /
  `@Inject` / `@InjectRepository` injection and
  `@Module` registrations → `DEPENDS_ON` (tsyringe / InversifyJS / TypeDI constructors too),
  `@Cron/@Interval/@Timeout` and Bull `@Process` → `SCHEDULED_AS`,
  `@OnEvent` / `@EventPattern` / `@MessagePattern` / `@SubscribeMessage` → `TRIGGERS`, guards /
  interceptors / pipes / filters and `consumer.apply(...)` middleware → `HOOKS`; **Express, Fastify,
  koa-router and Hono**: router calls → `ROUTES_TO` to the named handler, with mounts
  (`app.use('/api', router)`, `fastify.register(plugin, { prefix })` with a named or inline plugin, koa
  prefixes and `use(prefix, sub.routes(), sub.allowedMethods())`, Hono `basePath` / `app.route`) composed
  across files, NodeNext `./x.js` specifiers, tsconfig `paths` / `baseUrl` names, Node.js subpath imports
  (`#src/*`), workspace packages and barrels that re-export a router included; routers typed with a
  framework type (`r: Router`,
  `srv: FastifyInstance`) and class-based routers whose handlers live on an injected controller
  (`this.users.getUser`, paths such as `` `${this.path}/:id` `` folded from literal fields and consts); a
  handler held by a class property (`getUser = asyncHandler(async (req, res) => …)`) gets its own
  `:Method` node; **Next.js**: `app/**/route.ts` method exports and `pages/api/**`
  default exports → `ROUTES_TO` with the folder path under the project root (`next.config.*`); **TypeORM /
  MikroORM**: relations (MikroORM `Ref` / `LazyRef` / `IdentifiedReference` types and `new Collection<X>(this)`
  initializers included) →
  `HAS_MANY` / `REFERENCES`, **`onDelete: 'CASCADE' | 'SET NULL'` (MikroORM `deleteRule`) and a remove
  `cascade` → `CASCADES`**
  traversed by `deepmap_impact` (an `onDelete` sits on the foreign key, so its principal is the referenced
  entity: `deepmap_impact("Owner")` lists the `Cat` whose `@ManyToOne(() => Owner, …, { onDelete:
  'CASCADE' })` it holds), entity listeners → `HOOKS`; **Angular**: `Routes` (annotated, or a const handed
  to `RouterModule.forRoot/forChild` / `provideRouter`) → `ROUTES_TO`, route guards → `HOOKS`, constructor
  and `inject()` DI → `DEPENDS_ON`; `extends` / `implements` → `INHERITS` /
  `IMPLEMENTS`. An imported type, guard, handler or controller object binds in the module that defines it:
  imports are resolved against the tree the way TypeScript does (relative paths, tsconfig / jsconfig `paths`
  and `baseUrl`, the package.json `imports` map, workspace packages, barrels and default exports followed),
  and a name from a package the tree does not hold binds nothing. Compiled JavaScript with a tsc / Babel /
  bundler runtime marker or a `//# sourceMappingURL=` trailer is skipped (output with neither reads like
  source).
  Heuristic, not a TypeScript compiler; limits in `docs/known-limitations.md`.
- **Other languages**: structure + `:CALLS` only, no framework recognizer yet (the registry is
  the extension point; one flat module + one entry adds a language).

## What is *not* claimed

- **No engine-side framework-awareness / DI-binding / runtime-association discovery.** That is the
  engine's optional LLM-enrichment path; DeepMap does not run it (`--no-cluster`, no LLM). The
  framework edges above are DeepMap's OWN offline recognizers, currently Python + Ruby/Rails +
  C#/.NET + Java (Spring/JPA) + Go (Gin/GORM) + TypeScript/JavaScript (NestJS, Express, Fastify,
  koa-router, Hono, Next.js, TypeORM, Angular).
- **No community clustering** (Leiden) in the shipped graph; it is also skipped by `--no-cluster`.
- **Routes** are only typed as `:Route` on the Python `ast` path (decorator-based). The default
  multi-language engine exposes no decorator info, so route handlers map to `:Function` there.
- Engine detail: `build/graphify_extract.py` (how the engine is run and how its output is mapped). The
  multi-language fixtures that `tests/graphify_multilang_gates.py` reads were captured from the real
  engine under a socket tripwire (zero egress); known gaps are in [`known-limitations.md`](known-limitations.md).
