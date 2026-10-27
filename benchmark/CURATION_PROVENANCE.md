# Benchmark curation — adversarial-verification audit trail

Per-question evidence backing the **adversarially verified** claim for the ground-truth in
`questions.yaml`. Each question was authored by one agent and INDEPENDENTLY verified by a separate
agent that re-read the real source at the pinned SHA and confirmed every `file:line` atom + that grep
genuinely can/cannot reconstruct the answer (curator != verifier). **Sign-off state: COMPLETE
(2026-07-08).** Acceptance was a fully automated, independent LLM-as-judge re-verification of all 334 atoms
at their exact `file:line` (one `click-concept-2` relabel applied, zero unresolved defects), so this ground
truth is **SIGNED OFF** (see the "Sign-off state" addendum at the end of this file). No benchmark number is
published yet: the internal runs measured earlier DeepMap graphs, so a run on the current release comes
first (`benchmark/run_g3_n4.sh`, which reuses this ground truth unchanged, because it describes the pinned
repos, not DeepMap's graph). Notes trimmed to the key finding.

## fastapi/fastapi @ 702fea83bb3e

### fastapi-blast-1  ·  multi_hop  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  13 atoms
Verified at SHA 702fea83 (clone root is the fastapi/ package dir; real paths are dependencies/utils.py and
routing.py — atom file prefixes 'fastapi/...' match the repo layout). All 13 atoms confirmed at their exact
lines.  Direct call sites: grep for solve_dependencies returns EXACTLY 3 calls (utils.py:649 recursive self-
call; routing.py:473 HTTP app; routing.py:767 WS app) plus the import at routing.py:59 — candidate's
enumeration is complete, no missed call site. solve_dependencies is keyword-only (def at 598 has `*,`), so
renaming any kw param breaks all sites. SolvedDependency dataclass at 590-595 has exactly fields
values/errors/background_tasks/response/dependency_cache; construction a

### fastapi-concept-1  ·  concept_to_impl  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  9 atoms
Verified against fastapi @702fea8 (clone root IS the fastapi/ package dir, so candidate's
`fastapi/dependencies/utils.py` == `dependencies/utils.py`). Note this is a heavily-modified/forked fastapi
(utils.py 40KB, routing.py 253KB; has SSE/JSONL streaming paths) but the cited mechanism is intact.  ANSWER
CORRECT: Both halves implemented as described. (1) Creation+injection: solve_dependencies (utils.py:598) lines
719-722 — `if dependant.background_tasks_param_name:` / `if background_tasks is None: background_tasks =
BackgroundTasks()` (721) / `values[...] = background_tasks` (722). Recognized by
lenient_issubclass(StarletteBackgroundTasks) at 377-378; carried on SolvedDependency.background_t

### fastapi-concept-2  ·  concept_to_impl  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  6 atoms
Implementation confirmed correct. serialize_response (routing.py:293) does the trimming/coercion:
field.validate (line 309 / 311 threadpool) coerces+prunes the returned object to the declared response
ModelField type, raising ResponseValidationError on mismatch; field.serialize (line 322, after `serializer =
field.serialize_json if dump_json else field.serialize` at 321) applies
include/exclude/by_alias/exclude_unset/exclude_defaults/exclude_none; with no field it falls back to
jsonable_encoder (line 333). Invoked on the NON-streaming branch (the else where raw_response is not a
Response) from the per-request app closure built by get_request_handler (def line 367), at `await
serialize_respon

### fastapi-dependency-overrides-1  ·  dependency-overrides  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  6 atoms
Verified against fastapi/fastapi @ 702fea8 (release 0.138.2). The package sits at repo root, so the in-package
path fastapi/dependencies/utils.py maps to ./dependencies/utils.py — confirmed the file. All six atoms verify
at the exact claimed lines: - get_dependant def @ 286 (correct). - Recursive per-Depends-param build @ 334:
`sub_dependant = get_dependant(... call=param_details.depends.dependency ...)` inside the param loop, appended
at 343. One recursive call per Depends param — correct. - solve_dependencies def @ 598 (it's `async def`;
candidate said "def at 598" — fine). - Override block @ 632-647: `original_call = sub_dependant.call` (636);
`call = getattr(dependency_overrides_provider

### fastapi-effective-route-context-1  ·  effective-route-context  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  7 atoms
All 7 atoms verified at their EXACT claimed line numbers against the live file (SHA
702fea83bb3ef0b558104835d7936af49650fb2d). Note: the repo clone root IS the package dir, so the file is at
routing.py (not fastapi/routing.py) on disk, but the logical path fastapi/routing.py is correct.  Mechanism
confirmed line-by-line: - CONNECTING OBJECT: module-global ContextVar `_effective_route_context_var =
ContextVar(...)` at line 831 (831-833). Confirmed. - CONSUMER: APIRoute.get_route_handler (def 1185) calls
`_effective_route_context_var.get()` at 1189; guard `if effective_context is not None and
effective_context.original_route is self` at 1190; then `route = cast(_APIRouteLike, effective_context

### fastapi-security-scopes-1  ·  security-scopes  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  6 atoms
All six atoms verified at the exact cited lines in the real source (clone root is ...\clones\fastapi, files
under fastapi/). Accumulation property: Dependant.oauth_scopes cached_property (models.py:53-60) unions
parent_oauth_scopes.copy() + own_oauth_scopes preserving order/deduping. Threading confirmed: get_dependant
computes current_scopes = (parent or []) + (own or []) at utils.py:305 and passes
parent_oauth_scopes=current_scopes at utils.py:339; Security sub-deps' own scopes are read from
params.Security at utils.py:330-333. Request-time injection confirmed: solve_dependencies assigns
values[security_scopes_param_name] = SecurityScopes(scopes=dependant.oauth_scopes) at utils.py:726-727;

### fastapi-sse-format-1  ·  sse-format  ·  verdict=KEEP  ·  grep_cannot_reconstruct=False  ·  1 atoms
Verified independently. `def format_sse_event(` is at fastapi/sse.py:159 exactly (confirmed by Read; the clone
source root is the fastapi package dir itself, so the repo-relative path fastapi/sse.py is correct). The
contextual claim also checks out: format_sse_event is imported in routing.py:74 and called at routing.py:531
and 541 inside the SSE streaming branch helper `_serialize_sse_item`, which sits within `get_request_handler`
(def at routing.py:367). This is a single-hop lookup; grep can reconstruct it directly (sse.py:159), so
grep_cannot_reconstruct is correctly FALSE for this single control — still KEEP.

## pallets/click @ 679a7a0eccbd

### click-blast-1  ·  multi_hop  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  11 atoms
All 11 atoms verified at exact lines against pallets/click @ 679a7a0. Context.invoke is at core.py:853 and
contains the four contract elements the question names: the `isinstance(callback, Command)` default-fill
branch (calls param.get_default at :887 / type_cast_value at :897), the UNSET-to-None handling (:895-896, with
the issue-3066 comment), the ctx.params.update(kwargs) tracking (:901), and the `with ctx:` sub-context push
(:906). Each named reverse-dependent confirmed: DIRECT (1-hop) Command.invoke:1353
(ctx.invoke(self.callback,**ctx.params) at :1367), Context.forward:909 (self.invoke(cmd,...) at :926 after
copying parent params), Group.invoke:1950 (_process_result closure calls ctx.i

### click-concept-1  ·  concept_to_impl  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  7 atoms
All 7 cited atoms verified at exact line numbers in the cloned click repo. The implementation chain is correct
and complete:  - Parameter.handle_parse_result (core.py:2677) is the parse-result handler; it calls
process_value at core.py:2725 (the `value = self.process_value(ctx, value)` inside a try, with resilient-
parsing fallback to UNSET). - Parameter.process_value (core.py:2550) calls type_cast_value at core.py:2572. -
Parameter.type_cast_value (core.py:2476) is the per-parameter coercion driver AND the single-vs-multi dispatch
hop: it branches on nargs==1/is_composite vs nargs==-1 vs nargs>1 vs self.multiple, building a `convert`
closure that calls self.type(value, param, ctx). - ParamTy

### click-concept-2  ·  concept_to_impl  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  6 atoms
All six candidate atoms verified at exact line numbers in the real source.  (a) Precedence/fallback:
Parameter.consume_value (core.py:2428). When the parser produced nothing (value is UNSET, line 2451) it calls
value_from_envvar (line 2452) and on non-None sets source=ParameterSource.ENVIRONMENT (line 2455), verified
ahead of default-map (2457-2461) and static default (2468-2472). Candidate's range 2451-2455 is exact.  (b)
Lookup: Parameter.resolve_envvar_value (core.py:2616) reads os.environ for configured name(s), returns first
non-empty (2641-2659). Option subclass extends with auto-built name
f'{ctx.auto_envvar_prefix}_{self.name.upper()}' at core.py:3415 (gate 3414) — candidate's 3414-3

### click-group-dispatch-1  ·  group-subcommand-dispatch  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  6 atoms
Independently read core.py at SHA 679a7a0. Every cited line is EXACT. Verified the full runtime chain: (1)
Group.parse_args def at 1936; split `ctx._protected_args, ctx.args = rest[:1], rest[1:]` at 1946 (non-chain
branch). (2) Group.invoke def at 1950; `args = [*ctx._protected_args, *ctx.args]` at 1967; non-chain
`cmd_name, cmd, args = self.resolve_command(ctx, args)` at 1978. (3) resolve_command def at 2018; `cmd_name =
make_str(args[0])` at 2021; `cmd = self.get_command(ctx, cmd_name)` at 2024 (answer correctly attributes the
2024 cite to the get_command call); token_normalize retry at 2028-2030; `raise NoSuchCommand` at 2041 — all
exact. (4) Group.get_command `return self.commands.get(cm

### click-param-binding-1  ·  decorator-to-command-param-binding  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  6 atoms
Independently read pallets/click@679a7a0eccbd src/click/decorators.py. Every atom line number is exact and confirmed:
- option's decorator calls _param_memo(f, cls(param_decls, **attrs)) at line 374, returns f unchanged at 375.
argument's at 346, returns f at 347. (Candidate prose also cites 346/374 correctly.) - _param_memo def at 314;
Command-branch f.params.append(param) at 316; f.__click_params__ = [] at 319; f.__click_params__.append(param)
at 321. All correct. - command's inner decorator(f): decorator_params = f.__click_params__ at 225; del
f.__click_params__ at 229; params.extend(reversed(decorator_params)) at 230; cmd = cls(name=..., callback=f,
params=params, **attrs) at 248. All correct.

### click-value-resolution-1  ·  parameter-value-resolution-and-eager-order  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  6 atoms
Independently read the source at SHA 679a7a0. All claims verified exactly.  PRECEDENCE LADDER (core.py
consume_value, def at 2428): value = opts.get(self.name, UNSET) as COMMANDLINE (2442-2449); if UNSET ->
value_from_envvar -> ENVIRONMENT (2451-2455); if UNSET -> ctx.lookup_default -> DEFAULT_MAP (2457-2461, with
str-split extension to 2466); if UNSET -> get_default -> DEFAULT (2468-2472). Order
COMMANDLINE>ENVIRONMENT>DEFAULT_MAP>DEFAULT confirmed and matches the docstring at 2433-2435. Correct.
DEFAULT_MAP RECURSION: lookup_default (def 781) reads value = self.default_map[name] at 796 (candidate cited
796 - exact); default_map inherited via parent.default_map.get(info_name) at 388 inside

## pallets/flask @ 36e4a824f340

### flask-blast-1  ·  multi_hop  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  16 atoms
Read the real source at the pinned SHA and confirmed all 16 in-tree ensure_sync references (grep). The core
thesis is correct: ensure_sync (app.py:1065) returns a callable that every site immediately invokes, so a
return-shape change breaks the request lifecycle.  DIRECT invokers verified verbatim: dispatch_request view-
invocation (990, the every-endpoint hop), handle_user_exception ensure_sync(handler)(e) (895),
handle_http_exception (863), handle_exception 500-handler (946), preprocess_request before_request (1387),
process_response after_request (1408 & 1413), do_teardown_request (1446), do_teardown_appcontext (1474),
shell-context (616); cross-module View.as_view.view (110/116), MethodVi

### flask-blueprint-3  ·  blueprint  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  6 atoms
Independently read all three source files at the pinned SHA. Every atom's file/symbol/line is exact:  -
scaffold.py:336 `def route` -> decorator calls self.add_url_rule(rule, endpoint, f, **options) at line 362.
Confirmed. - blueprints.py:433 `self.record(` with `lambda s: s.add_url_rule(...)` at 434;
Blueprint.add_url_rule decorated at 412/def 413. Confirmed. - Blueprint.record at def 224, append to
self.deferred_functions at 230. Confirmed (answer's text citations 224/230 correct). - app.register_blueprint
def at app.py:570, body `blueprint.register(self, options)` at 595; Blueprint.register def at
blueprints.py:273. Confirmed. - blueprints.py:334 `for deferred in self.deferred_functions:`

### flask-concept-1  ·  concept_to_impl  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  6 atoms
All six atoms verified against the real source. WRITE/sign: save_session at sessions.py:373 calls
get_signing_serializer(app).dumps(dict(session)); the serializer is built at sessions.py:303 as a
URLSafeTimedSerializer (line 313) keyed on app.secret_key with key_derivation='hmac' (itsdangerous HMAC).
READ/verify: open_session at sessions.py:332 calls s.loads(val, max_age=...) and the except BadSignature at
sessions.py:334 returns a fresh empty self.session_class() — exactly the 'silently discard altered data'
behavior. BadSignature is imported from itsdangerous (sessions.py:10). Lifecycle: process_response drives
save_session at app.py:1416 (reached via finalize_request, whose call is at app

### flask-concept-2  ·  concept_to_impl  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  9 atoms
Fully verified against real source. The dispatch branch is app.py:1325 (`elif isinstance(rv, (dict, list)):`)
-> 1326 (`rv = self.json.response(rv)`). `self.json` resolves to a DefaultJSONProvider instance:
sansio/app.py:227 sets `json_provider_class = DefaultJSONProvider`, and :326 sets `self.json =
self.json_provider_class(self)`. DefaultJSONProvider.response (provider.py:189) builds
`self._app.response_class(f"{self.dumps(...)}\n", mimetype=self.mimetype)` at lines 213-214 where
mimetype="application/json" (line 163-164), and chooses indent(2) vs compact separators based on debug/compact
(208-211) — candidate's "compact vs indented based on debug mode" is accurate. dumps (provider.py:166)

### flask-dispatch-1  ·  dispatch  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  7 atoms
Independently verified against Flask src/flask/app.py at SHA 36e4a824f340fdee7ed50937ba8e7f6bc7d17f81. Every
hop and line citation in the candidate answer is exact.  Chain confirmed: - Flask.__call__ at line 1618; body
is `return self.wsgi_app(environ, start_response)` at line 1625 (docstring only besides). ✓ - Flask.wsgi_app
at 1566: ctx = self.request_context(environ) at 1592; ctx.push() at 1596; response =
self.full_dispatch_request(ctx) at 1597 (inside nested try). ✓ - Flask.full_dispatch_request at 992:
request_started.send at 1013; rv = self.preprocess_request(ctx) at 1014; `if rv is None: rv =
self.dispatch_request(ctx)` at 1015-1016; except -> rv = self.handle_user_exception(ctx, e)

### flask-helper-control-4  ·  helper  ·  verdict=KEEP  ·  grep_cannot_reconstruct=False  ·  2 atoms
Independently read src/flask/helpers.py. The module-level url_for is defined at line 200 (def
url_for(endpoint, *, _anchor=None, _method=None, _scheme=None, _external=None, **values) -> str). After the
docstring, its body is exactly one statement: a return of a delegating call to current_app.url_for, written
multi-line at lines 244-251: return current_app.url_for(endpoint, _anchor=_anchor, _method=_method,
_scheme=_scheme, _external=_external, **values). The candidate's single-line compression is argument-for-
argument identical (endpoint passed positionally, the four keyword params forwarded, **values spread). Both
atoms verified at the exact current line numbers (200 and 244). This is a sin

### flask-proxy-2  ·  proxy  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  6 atoms
Independently read globals.py, ctx.py, and app.py at the pinned source. Every atom and line number is exact: -
globals.py:40 `_cv_app: ContextVar[AppContext] = ContextVar("flask.app_ctx")` ✓ - globals.py:57 `request:
RequestProxy = LocalProxy(...)` with `_cv_app, "request", unbound_message=_no_req_msg` spanning 57-59 ✓ -
ctx.py:346 `request = app.request_class(environ)` inside AppContext.from_environ (def at 340) ✓ - ctx.py:320
`self._request ... = request` in __init__ ✓ (cited correctly in prose) - ctx.py:370 `@property` / 371 `def
request` returning self._request (379), raising RuntimeError("There is no request in this context.") at 377 ✓
- ctx.py:433 `self._cv_token = _cv_app.set(self)` i

### flask-strlit-control-1  ·  single_hop_control  ·  verdict=KEEP  ·  grep_cannot_reconstruct=False  ·  2 atoms
Verified against real source at src/flask/json/provider.py. Line 105: `return
self._app.response_class(self.dumps(obj), mimetype="application/json")` is inside JSONProvider.response (the
BASE class, not DefaultJSONProvider as the candidate states for line 105 — minor symbol mislabel, see below) —
a genuine code literal assigned as the mimetype kwarg. CONFIRMED. Line 163: `mimetype = "application/json"` is
a code class attribute of DefaultJSONProvider. CONFIRMED. The two atoms are the only code-literal assignments;
all other grep hits (app.py:1549, provider.py:91, provider.py:192, json/__init__.py:140, testing.py:42) are
inside docstrings — correctly excluded. Note DefaultJSONProvider.respons

## pallets/werkzeug @ 1b00618e787f

### werkzeug-blast-1  ·  multi_hop  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  9 atoms
Read all three source files in full and verified every atom's exact line and every behavioral claim.  ATOM
LINES — all 9 exact: - MapAdapter.match: `def match(` at map.py:528 (the real impl; overloads at 508/518).
Correct. - MapAdapter.dispatch: map.py:448. Correct. Unpacks `endpoint, args = self.match(...)` at 499,
catches RequestRedirect at 500, returns view_func(endpoint, args) at 502. - MapAdapter.test: map.py:707.
Correct. self.match() at 717; RequestRedirect→pass (718-719), HTTPException→False (720-721). -
MapAdapter.allowed_methods: map.py:724. Correct. self.match(method="--") at 730;
MethodNotAllowed→e.valid_methods at 731-732. - Map.bind: map.py:193. Correct. Constructs/returns MapA

### werkzeug-concept-1  ·  concept_to_impl  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  11 atoms
All cited atoms verified against the real source. Accept.best_match (accept.py:210) IS the selection logic the
question describes: it iterates over server-offered `matches`, finds each one's best client match via
_best_single_match (199), computes _specificity (71; MIMEAccept override 259), and keeps the entry with the
highest (quality, specificity) — "picks the single best fit by preference and how specific each option is."
The per-class comparison primitives _value_matches (base 75, MIMEAccept 262, LanguageAccept 329) and
from_header (149, parses the client preferences) all match. Request.accept_mimetypes (cached_property
decorator at request.py:402, returns MIMEAccept.from_header) and acc

### werkzeug-concept-2  ·  concept_to_impl  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  10 atoms
All 10 atom line numbers verified exact against source. The trace is faithful: Request.form (request.py:450) /
Request.files (request.py:491) both call _load_form_data (266), which builds the parser via
make_form_data_parser (240) and calls FormDataParser.parse (281-286 -> parse at 218). For multipart/form-data,
parse dispatches to _parse_multipart (240->263), which reads the boundary (270) and drives
MultiPartParser.parse (284-285 -> 393). MultiPartParser.parse instantiates MultipartDecoder (401), feeds
chunks via receive_data (411->127) and pulls events via next_event (412/455->140). The actual delimiter-
separated section walking lives in MultipartDecoder.next_event (140) + its _parse_data

### werkzeug-converters-int-4  ·  routing-converters  ·  verdict=KEEP  ·  grep_cannot_reconstruct=False  ·  1 atoms
Verified directly against pallets/werkzeug@1b00618e787f
src/werkzeug/routing/converters.py.
Line 173: `class IntegerConverter(NumberConverter):`. Line 195: `regex = r"\d+"`. Both the value (r'\d+') and
line numbers (class at 173, regex at 195) in the candidate answer are exact. File path correct. This is a
single-hop control question — grep can reconstruct it directly (rg 'regex = r' finds line 195 within the
IntegerConverter class), so grep_cannot_reconstruct is correctly FALSE; that is expected for a single-hop
control and does not warrant DROP. Note: NumberConverter base sets

### werkzeug-routing-exc-2  ·  routing-exceptions  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  6 atoms
Independently verified all claims against the source.  CASE (A): _match closure raises SlashRequired() at
matcher.py:114 (class SlashRequired(Exception) at matcher.py:17). StateMachineMatcher.match catches it (except
SlashRequired at matcher.py:176) and re-raises RequestPath(f"{path}/") from None at matcher.py:177.
MapAdapter.match catches RequestPath (map.py:647) and raises RequestRedirect(self.make_redirect_url(...)) at
map.py:650-652. RequestRedirect is defined at routing/exceptions.py:51 with code = 308 (line 58), subclassing
HTTPException. Chain SlashRequired -> RequestPath -> RequestRedirect (HTTP 308) CONFIRMED.  CASE (B): matcher
raises NoMatch(have_match_for, websocket_mismatch) at

### werkzeug-routing-match-1  ·  routing-match  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  6 atoms
All three hops verified against source. (1) MapAdapter.match (real impl def at map.py:528) delegates via
self.map._matcher.match() at map.py:646; _matcher is a StateMachineMatcher set in Map.__init__ at map.py:115.
StateMachineMatcher.match def is at matcher.py:75; the recursive walk is the nested closure _match() defined
at matcher.py:85, which compiles+matches test_part.content at matcher.py:136
(`re.compile(test_part.content).match(target)`). CONFIRMED. (2) test_part.content is assembled by
Rule._parse_rule() (generator def at rules.py:605); at rules.py:638 it embeds
`(?P<__werkzeug_{convertor_number}>{convobj.regex})` (candidate paraphrased the number as N — semantically
correct). convob

## psf/requests @ 23953c0c8752

### requests-concept-1  ·  concept_to_impl  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  10 atoms
All 9 candidate atom line numbers verified exact against the real source. The implementation correctly
realizes the described connection-recycling capability: init_poolmanager (adapters.py:239) builds the
recycling store via PoolManager(...) (262); get_connection_with_tls_context (455) fetches a per-host pooled
connection through self.poolmanager.connection_from_host(...) (506) (proxy variant at 501); send (634) obtains
conn (662) and drives it via conn.urlopen(...) (696). default_headers (utils.py:951) emits "Connection":
"keep-alive" (960). The answer's honest caveat is accurate — requests delegates the actual socket-reuse
bookkeeping to urllib3's PoolManager; this module only constructs,

### requests-concept-2  ·  concept_to_impl  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  8 atoms
All 8 atoms verified at exact cited line numbers against the real source at SHA 23953c0. (1) models.py:914 is
the `def iter_content` runtime line; :939 is `yield from self.raw.stream(chunk_size, decode_content=True)`
exactly. (2) `content` property: `@property` is at 1034, `def content` at 1035 (atom cites the def line —
correct); the join expression is exactly at 1046. (3) utils.py:951 is `def default_headers`; :958 is the
`"Accept-Encoding": DEFAULT_ACCEPT_ENCODING` line; :91 begins the `DEFAULT_ACCEPT_ENCODING` assignment using
urllib3's make_headers(accept_encoding=True). (4) adapters.py:704 is `decode_content=False,` inside
conn.urlopen() in send() — exact.  Correctness: The answer accu

### requests-digest-hooks-3  ·  hook-dispatch-digest  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  8 atoms
All eight atoms verified against the real code at the cited line numbers. Chain confirmed:
PreparedRequest.prepare (models.py:445) -> prepare_auth which runs auth_handler(self) at models.py:691 ->
HTTPDigestAuth.__call__ (auth.py:321) registers self.handle_401 via register_hook (auth.py:339) ->
RequestHooksMixin.register_hook appends to self.hooks['response'] (append at models.py:266; method def at 257)
-> Session.send reads hooks=request.hooks (sessions.py:775) and calls dispatch_hook (sessions.py:791) ->
dispatch_hook iterates and invokes hook(hook_data) (hooks.py:45), replacing the response if non-None
(hooks.py:46-47) -> handle_401 (auth.py:273) on a 401+digest challenge resends via r.co

### requests-redirect-auth-2  ·  redirect-credential-stripping  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  9 atoms
All 8 candidate atoms verified at the exact cited line numbers in the real code (psf/requests @ 23953c0c).
Answer is fully correct:  1. Auth-drop decision: rebuild_auth (sessions.py:309) checks `if 'Authorization' in
headers and self.should_strip_auth(original_url, url): del headers['Authorization']` at lines 324-327.
should_strip_auth (line 154) returns True when `old_parsed.hostname != new_parsed.hostname` (line 158) — so a
cross-host 302 strips Authorization. Correct. The candidate also correctly notes the http->https/standard-port
carve-out and other scheme/port-change cases (lines 164-184) and the netrc re-application (lines 330-332).  2.
Method change: rebuild_method (line 370) turns a

### requests-strip-auth-control-4  ·  redirect-credential-stripping  ·  verdict=KEEP  ·  grep_cannot_reconstruct=False  ·  3 atoms
Verified against the real code at psf/requests@23953c0c8752 src/requests/sessions.py. Every claim and line number
in the candidate answer is exact: should_strip_auth defined at line 154 with signature (self, old_url,
new_url) -> bool; hostname mismatch -> True at line 158; http->https standard-port special case returning
False at lines 164-170; scheme-unchanged + both default ports -> False at lines 176-181; final `return
changed_port or changed_scheme` at line 184. Call site confirmed: line 324 inside rebuild_auth (def at line
309): `if "Authorization" in headers and self.should_strip_auth(original_url, url): del
headers["Authorization"]`. All three candidate atoms confirmed at the stated lines

### requests-transport-1  ·  transport-dispatch  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  9 atoms
All 9 atoms verified at the exact line numbers claimed; nothing drifted. The answer's runtime-dispatch
reasoning is fully correct: Session.send (sessions.py:784) calls adapter.send on the object returned by
get_adapter (sessions.py:778); get_adapter (sessions.py:870) iterates self.adapters (an OrderedDict) and
returns the first adapter whose registered prefix matches via url.lower().startswith(prefix.lower())
(sessions.py:877); that dict is populated by Session.__init__'s self.mount('https://', HTTPAdapter())
(sessions.py:502, mount body at 888-897), so for https the concrete object is HTTPAdapter. BaseAdapter.send is
abstract (adapters.py:151); the body that runs is HTTPAdapter.send (adapte

---

## Multi-language breadth (added 2026-07-06) — independent verifier audit

16 new questions (4 repos × 4 fair families) authored by per-repo curator agents, then audited by a
SEPARATE independent verifier agent (curator ≠ verifier ≠ scorer) against the pinned clones. Automated
locator check first confirmed **101/101 atoms exist at their exact file:line**; the verifier then re-derived
every atom from source, ran ripgrep grep-honesty checks, and independently enumerated the symbol-lookup call
sites. **Result: 14 KEEP · 2 FIX (applied) · 0 RELABEL.** All four `single_hop_control` questions confirmed
genuinely grep-winnable; all `grep_can_do:false` labels confirmed honest.

### httprouter-concept-1  ·  param/catch-all matching  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  9 atoms
All atoms confirmed in tree.go/router.go (getValue@326, static guard@337, param switch@354, segment scan@358, Param Key n.path[1:]@371/372, catchAll@401, Key n.path[2:]@411, handle read@390). Request-time ordering is not a string match. Range atoms were normalized to their start line by the assembler.

### httprouter-transitive-1  ·  request→handler chain  ·  verdict=FIX (applied)  ·  grep_cannot_reconstruct=True  ·  7 atoms
Endpoints correct (ServeHTTP@463, trie select@470, getValue call@471, getValue@326, sink handle()@473). Verifier caught a path inconsistency: the set paired the STATIC-descent hop tree.go:341 with the PARAM-branch handle read tree.go:390 (mutually exclusive in one walk). FIX applied: tree.go:390 → **tree.go:426** (the exact-match leaf handle read on the static path); prose updated. Now a consistent static-route trace.

### httprouter-control-1  ·  Param/Params/ByName defs  ·  verdict=KEEP  ·  grep_cannot_reconstruct=False (control)  ·  3 atoms
router.go:92/100/104 confirmed unique definition tokens; genuine grep-winnable control. No params.go at this SHA (confirmed).

### httprouter-symbol-1  ·  callers of node.getValue  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  3 atoms
`rg getValue` (non-test) → exactly the 3 library callers (Lookup@396, allowed@432, ServeHTTP@471); definition (tree.go:326) and 6 test hits correctly excluded.

### gin-concept-1  ·  middleware chain execution/abort  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  7 atoms
Next@198, invoke@202, index int8@68, abortIndex@57, Abort@217, combineHandlers@241, kickoff@722 all confirmed. Mechanism spans 4 symbols across 3 files — not a string match.

### gin-transitive-1  ·  ServeHTTP→handler chain  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  9 atoms
Full ordered chain verified: ServeHTTP@662 → handleHTTPRequest call@672/def@690 → getValue call@715/def tree.go:418 → handlers assign@720 → c.Next()@722 → Next@198/invoke@202. Accurate and ordered.

### gin-control-1  ·  GET/POST/handle defs  ·  verdict=KEEP  ·  grep_cannot_reconstruct=False (control)  ·  3 atoms
routergroup.go GET@116/POST@111/handle@86 confirmed; genuine control.

### gin-symbol-1  ·  callers of Context.Next  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  5 atoms
`rg .Next()` (non-test) → exactly 5 *gin.Context sites (gin.go:722/766, logger.go:214/282, recovery.go:90); each receiver + enclosing function verified. Context.Next defined once (context.go:198).

### serdejson-concept-1  ·  string escaping  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  9 atoms
serialize_str@190 → format_escaped_str@2069 → contents@2079 scanning ESCAPE table@2147; begin_string@1737/write_string_fragment@1757/write_char_escape@1766/end_string@1747; empty CompactFormatter impl@1941. Escaping runs via DEFAULT trait methods (empty impl) → dispatch not textually visible.

### serdejson-transitive-1  ·  from_str number→Value  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  9 atoms
Full chain verified for from_str("123")→Value: from_str@2709 → from_trait@2507 → Value deserialize (value/de.rs:24) → deserialize_any@1393 (digit branch@1424) → parse_any_number@933 (default cfg) → parse_integer@462 → parse_number@509 (ParserNumber::U64) → ParserNumber::visit@118 (U64→visit_u64@124) → ValueVisitor::visit_u64 (value/de.rs:56 → Value::Number). cfg-gated default path correctly chosen.

### serdejson-control-1  ·  Value enum defs  ·  verdict=KEEP  ·  grep_cannot_reconstruct=False (control)  ·  4 atoms
value/mod.rs pub enum Value@116, Number@142, Object@175, Null@124; `rg pub enum Value` unique. Genuine control.

### serdejson-symbol-1  ·  callers of Read::parse_str  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  5 atoms
`rg .parse_str(` → exactly 5 call sites, all de.rs (306 peek_invalid_type, 1428 deserialize_any, 1539 deserialize_str, 2221 MapKey deserialize_any, 2267 MapKey deserialize_bool); per-reader trait impls (excluded by the question) correctly not counted. (Minor prose-only mislabel of read.rs:790 as "Fused" vs the &mut R blanket impl — not an atom, no scoring impact.)

### zod-concept-1  ·  parse validate/collect/throw  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  9 atoms
parse@30, _parse@16, run call@18, run wrapper@278, runChecks@218, $ZodString parse@360, invalid_type push@368, throw+finalizeIssue@23, finalizeIssue (util.ts:849) confirmed. Concept question explicitly covers the with-checks path, so citing the run wrapper is in scope. Dynamic _zod.run/_zod.parse field dispatch → grep-defeating.

### zod-transitive-1  ·  safeParse invalid→result  ·  verdict=FIX (applied)  ·  grep_cannot_reconstruct=True  ·  7 atoms
Endpoints correct (safeParse@73, run call@61, $ZodString parse@360, invalid_type push@368, issues.length branch@66, failed result@69). Verifier caught a control-flow error: for a NO-CHECK z.string(), the run wrapper schemas.ts:278 and dispatch schemas.ts:297 are BYPASSED — in $ZodType.init the checks.length==0 branch sets the deferred alias inst._zod.run = inst._zod.parse (schemas.ts:215), so dispatch lands straight in $ZodString parse@360. FIX applied: dropped 278 & 297, added schemas.ts:215; prose + notes corrected.

### zod-control-1  ·  error/schema class defs  ·  verdict=KEEP  ·  grep_cannot_reconstruct=False (control)  ·  4 atoms
$ZodError (errors.ts:248), $ZodRealError (errors.ts:250), $ZodAsyncError (core.ts:98), $ZodString (schemas.ts:357) — all names unique in v4-core. Genuine control.

### zod-symbol-1  ·  callers of util.aborted  ·  verdict=KEEP  ·  grep_cannot_reconstruct=True  ·  7 atoms
`rg util.aborted(` in v4/core → exactly 6 call sites, all schemas.ts (223/245/250 in runChecks, 264 handleCanaryResult, 2175 handleUnionResults, 2587 handleIntersectionResults) + the def (util.ts:815); sibling explicitlyAborted and payload.aborted property reads correctly excluded.

---

## Independent re-verification (2026-07-08) — automated, two-layer, a THIRD role

Acceptance was defined (2026-07-07) as the most comprehensive verification possible, judged by LLM, with no
human step. The full 49-question / 334-atom ground truth was independently
re-verified a SECOND time — by roles distinct from BOTH the original author and the prior verifier — with no
human atom-by-atom review. Two independent layers:

**Layer 1 — deterministic locator** (`benchmark/verify_atoms.py`; reproducible, no model / no network).
Resolves every `answer_key.ground_truth_set` atom to its exact `file:line` in the pinned clones and captures
the real source line. **Result: all 334 atoms resolve to an existing, in-range `file:line` — 0 FILE_MISSING,
0 LINE_OUT_OF_RANGE, 0 SYMBOL_MISSING.** 328/334 also auto-pass an on-topic identifier check; the 6 residual
soft-flags were manually confirmed correct (terse ContextVar `.get`/`.set` descriptions and the
`parse_str`/`ensure_sync` cases where the description names the enclosing method or the question's subject
symbol, not the exact-line token). Its report (`benchmark/results/ATOM_VERIFICATION.md` / `.json`) is not
shipped, because it quotes the pinned repos' source; re-create it with `python benchmark/verify_atoms.py`.

**Layer 2 — adversarial agent audit** (9 per-repo independent verifier agents + a synthesis pass). Each
repo's questions were re-derived from source at the pinned SHA by a fresh agent instructed to FALSIFY, with
in-clone ripgrep grep-honesty checks. **Result: 48/49 KEEP · 0 FIX · 1 RELABEL · 334/334 atoms confirmed at
their exact lines** — zero wrong atoms, zero impossible control-flow paths, every `single_hop_control`
genuinely grep-winnable, and every `grep_can_do:false` honest EXCEPT one:

- **click-concept-2 — RELABEL `grep_can_do` false→true (APPLIED 2026-07-08).** All six atoms are correct at
  their exact lines, but `rg envvar` recovers all six atom sites and the question's own `grep_partial_answer`
  concedes grep "lands directly on all six atoms" → reclassified as a grep-winnable control (questions.yaml
  `grep_can_do`, `oracle_provenance`, and `notes` updated).

Minor prose/anchor nuances were recorded as NON-defects (all KEEP, no atom moved, no number changed):
decorator-line-vs-def-line off-by-one within the "immediately around" tolerance (werkzeug-concept-1,
fastapi-effective-route-context-1); enclosing-class/handler-name prose slips (flask-blast-1, flask-concept-1);
and the httprouter-transitive-1 static-vs-param sink line (473 for a param route vs 476 for a bare-static
route — internally consistent, curator-note only). The prior two curator FIXes (httprouter-transitive-1,
zod-transitive-1) were independently re-confirmed correct in this pass.

**Sign-off state: COMPLETE (2026-07-08).** The acceptance mechanism is a comprehensive independent
**LLM-as-judge** re-verification (no separate human atom-by-atom step). That re-verification — the
two layers above — has been performed and **passed**: all 334 atoms confirmed at their exact `file:line`, the
single `click-concept-2` relabel applied, zero unresolved defects. **The ground truth is signed off.**
Benchmark numbers are therefore no longer gated on ground-truth verification. None is published yet: the
internal runs measured earlier DeepMap graphs, so each run is re-run on the current release first
(`benchmark/run_g3_n4.sh`; `benchmark/run_breadth.sh`), reusing this signed-off ground truth, and published
with its raw outputs. The honest framing still applies, e.g. multi-language breadth is *coverage + parity*,
not a lift.
