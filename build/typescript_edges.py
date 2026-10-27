#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TypeScript / JavaScript recognizer — NestJS, Express-style routers, Next.js, TypeORM, Angular ->
typed edges + ensured nodes (plugin #6).

graphify parses TypeScript and JavaScript at the node/call layer (classes, `Class.method` methods,
functions, file modules, :CALLS, :IMPORTS) but yields NO relationship for the wiring a Node.js service
actually runs on: decorator routes, constructor injection, scheduled jobs, event handlers, router
registrations, file-system routes and ORM relations are DECLARATIONS, not calls. This module reads those
conventions HEURISTICALLY (a small comment/string-aware lexer plus pattern matching — no tsc, no Node
runtime, not a compiler) and emits, per file:

  typed edges (the value):
    ROUTES_TO     NestJS (and routing-controllers) `@Controller(prefix)` + `@Get/@Post/@Put/@Patch/@Delete/
                  @All/@Options/@Head(path)` (and Nest's `@Sse` as GET, `@Search` as SEARCH) -> controller
                  class -> `Class.method` (byte-matching graphify's Method names), the full path composed
                  (`/cats/:id`); LOCAL, resolve-or-drop like the Java route path. On a class with no
                  @Controller (an abstract CRUD base) the path is the method's own and `prefix_unresolved`
                  says each subclass's prefix applies. Express / Fastify / koa-router / Hono router calls
                  `<router>.get(path, ...h)` (+ `router.route(path).get(h)` chains, `fastify.route({method,
                  url, handler})`, shorthand properties included, a koa named route, Hono `app.on(method,
                  path, h)`) -> this file's module -> the NAMED final
                  handler: a function of this file in scope at the route is LOCAL (a helper nested in another
                  function never shadows an import), an imported one NAME, a `this.m` / class `X.m` a `Class.m`
                  method, `x.m` on `x = new X()` `X.m` in the module X's import names, `this.users.list` on a
                  field whose class the field's type / `new X()` / constructor parameter property names
                  `X.list`, `ctrl.list` on an imported MODULE OBJECT (`ctrl = require('./ctrl')`, `import * as
                  ctrl`, `import ctrl from`, `import { ctrl }` through a barrel's `export { default as ctrl }
                  from './ctrl'` or an `export *` chain) `list` inside the module that holds the object only
                  (never by name across the repo): what the object holds under `list` — an entry the module
                  exports (`exports.list = ...`, `module.exports = { list: asyncHandler(list) }` being the
                  wrapped `list`, `export default { list: listUsers }`, a const's `{ ... }` and its later
                  `controller.list = ...`), the method of the class or instance (`new X()`, X declared,
                  imported or a class expression) it exports — `X.list`, or its base's when X inherits it (the
                  `extends` chain followed in the module and through an imported base) —, a class
                  expression's method (named bare, at its line) — or
                  nothing, never a private `function list` beside it; a shape not read keeps the name inside
                  that module (scope "module:"); a final handler
                  wrapped once (`asyncHandler(fn)`) is unwrapped at NAME; middleware before it is listed in
                  attrs; an inline function is anonymous (no node) and skipped. A path or prefix held by a
                  `const` in scope where it is read (the one binding of that name in the file: no other const
                  / let / var declarator of it, initialized or not, no for-of / for-in variable, parameter,
                  destructured name or catch clause; its block holding the read) or a literal `this.path`
                  field the class never assigns, or a template built only from those (`${this.path}/:id`), is
                  read as that literal. A call is a route only on a ROUTER
                  receiver (a variable or class field built by express() / Router() / new Router() /
                  new Hono() / Fastify(); a name, field or constructor parameter property typed with an app /
                  router type imported from that framework — `r: Router`, `srv: FastifyInstance`; or a
                  conventional app/router/server/fastify parameter in a file that uses an HTTP framework) —
                  `axios.get('/x', cb)`, `$.get(url, cb)`, `map.get(k)` never are. Mounts
                  (`app.use('/api', router)` — every router operand, so koa's `use('/users', users.routes(),
                  users.allowedMethods())` too —, the `[{ path, route }].forEach((r) => router.use(r.path,
                  r.route))` idiom over a literal array, `fastify.register(plugin, {prefix})` with the plugin
                  named or written inline, koa `new Router({prefix})`, Hono `basePath` / `app.route(path,
                  sub)`) compose the path — in this file here, across files in finalize(), through relative
                  imports (a NodeNext `./users.js` names users.ts), the tree's tsconfig `paths` / `baseUrl`,
                  Node.js subpath imports (package.json `imports`), workspace packages, barrels that re-export
                  the router, and otherwise an alias exactly one module of the tree matches. A mount in a test
                  file never shapes a router that a non-test file mounts.
                  Next.js `app/**/route.ts` exporting GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS and
                  `pages/api/**` default exports -> the file's module -> the exported handler, the path from
                  the folders (`(group)` dropped, `[id]` -> `:id`, `[...slug]` -> `*slug`, `[[...slug]]` ->
                  `*slug?`) under the project root's `app` (else `src/app`) / `pages` directory (finalize).
                  Angular `Routes` arrays (`{path, component, children}`, also an unannotated const handed to
                  RouterModule.forRoot / forChild or provideRouter) -> module -> the component class (method
                  VIEW).
    DEPENDS_ON    constructor injection in a DI class (@Injectable / @Controller / @Module / @Resolver /
                  @WebSocketGateway / @Catch / @Processor, Angular @Component / @Directive / @Pipe /
                  @NgModule, typedi @Service, tsyringe / InversifyJS @injectable): class -> each injected
                  class type (`@Inject(TOKEN)` keeps the token; `@InjectRepository(User)` /
                  `@InjectModel(Cat.name)` -> the entity), `@Inject` property injection, Angular `inject(X)`
                  fields, and @Module({imports, controllers, providers}) registrations (module -> X, like the
                  C# AddScoped registration edge).
    SCHEDULED_AS  @Cron / @Interval / @Timeout (@nestjs/schedule) and Bull @Process -> class -> `Class.method`.
    TRIGGERS      @OnEvent('name'), microservice @EventPattern / @MessagePattern, websocket @SubscribeMessage
                  -> class -> `Class.method`, the event/pattern in attrs (Java's @EventListener shape).
    HOOKS         @UseGuards / @UseInterceptors / @UsePipes / @UseFilters on a controller (src = the class)
                  or a handler (src = `Class.method`) -> the guard / interceptor / pipe / filter class, and
                  a module's `configure(consumer) { consumer.apply(M).forRoutes(...) }` -> the middleware
                  (event "middleware", the covered routes in attrs) — NestJS's request-lifecycle hooks, the
                  same meaning as a Rails before_action or a Flask before_request; TypeORM / MikroORM entity
                  listeners (`@BeforeInsert() normalize()`) -> class -> `Class.method` (a Rails before_save
                  callback's shape, LOCAL); Angular route guards (one guard on several routes: one edge,
                  `paths` listing them).
    HAS_MANY      TypeORM / MikroORM @OneToMany / @ManyToMany -> owner -> the related entity (from the
                  decorator, the property's type — `Collection<X>`, `X[]` — or a `new Collection<X>(this)`
                  initializer).
    REFERENCES    @ManyToOne / @OneToOne -> holder -> the referenced entity (also typed `Ref<X>`, `LazyRef<X>`,
                  `IdentifiedReference<X>`, `EntityRef<X>`).
    CASCADES      emitted principal -> dependent like every recognizer's (persisted REVERSED by edge_resolve,
                  so deepmap_impact(principal) lists what a delete reaches). `onDelete: 'CASCADE' | 'SET NULL'`
                  (MikroORM: `deleteRule`) is a DB foreign key, so its principal is the type the key
                  REFERENCES: on a @ManyToOne (or a @OneToOne that carries the @JoinColumn) -> the
                  referenced entity -> the declaring entity (a GORM belongs-to's shape: the src resolves
                  through edge_resolve's global :Class fallback, single candidate only, at most INFERRED).
                  An ORM-level remove cascade (`cascade: true`, or a list holding 'remove' / 'soft-remove' /
                  MikroORM's Cascade.REMOVE) -> owner -> element (the JPA CascadeType.REMOVE shape). 'insert' / 'update' / 'recover' alone, RESTRICT and
                  NO ACTION are not cascades; an onDelete on a @OneToOne without its @JoinColumn has no
                  knowable principal and emits nothing.
    INHERITS      class X extends Y, interface X extends Y (LOCAL when Y is declared here).
    IMPLEMENTS    class X implements Z.
                  graphify parses `extends` / `implements` but map_graph persists no relation for them, so
                  they come from here, as for Java and C#. Both carry their ends' declaration lines (a scope
                  hint "L<class>,<base>|"): the engine node-ifies an interface and a same-named `const` of one
                  file twice (zod v4's `interface ZodString` beside `const ZodString = ...`), and the lines
                  bind the interfaces instead of fanning out.

  supplemental nodes (the fallback): one ensured :Class node per top-level class / interface declaration,
  named BARE exactly as graphify names TypeScript types, with a deterministic body_summary. When graphify
  parsed the file, merge_supplemental's (name, source_file) dedupe drops these; when graphify missed the
  file they keep the controller / entity layer present + searchable. A class EXPRESSION, an anonymous
  default class and a class nested in a block are not node-ified (graphify does not node-ify them either).
  One ensured :Method node `Class.prop` per function-valued property of such a class (`list = async (req,
  res) => {...}`, `list = asyncHandler(async (req, res) => {...})` — a call-wrapped one only for a known
  handler wrapper or a wrapped function taking a handler's parameters, never `total = asyncComputed(...)`):
  graphify 0.8.39 node-ifies neither, 0.8.51 only the plain function (same name, so its node wins the merge),
  and routes bind these handlers.

Honesty model (the confidence wedge absorbs heuristic error):
  * a lexer blanks every comment, string, template literal and regex body BEFORE any matching (offsets and
    line numbers kept), so text inside them never becomes a decorator, a class or a route. It reads a `/` the
    way JavaScript does (a regex after an operator, a keyword or the `)` of an if / while / for / with head;
    a division after a value, a call's `)` or `x++`; a JSX closing tag's `</` is no regex) and, in a .tsx /
    .jsx / .js file, a backtick that follows a JSX tag on its line (its attribute expressions may hold `>`)
    with only plain words between them, a closing `</` after it, as text (a template after a comparison, in a
    call or inside another template's ${ } stays a template; each check reads 1,000 characters at most). A
    shape it still misreads (a stray backtick in JSX text that spans lines or follows punctuation, a
    division right after an object literal's `}`) can hide code and, rarely, let a template's text through;
  * every IMPORTED type, base class, guard, handler or module object is placed in the tree by finalize, the
    way TypeScript resolves it with what the tree holds: a relative path, the nearest tsconfig.json /
    jsconfig.json's `paths` and `baseUrl` (`extends` followed), a Node.js subpath import (`#src/x`) through
    the nearest package.json's `imports` (a conditions object in its own key order, the first target the tree
    holds), a workspace package by its package.json `name`; barrels (`export { X } from`, `export *`) and
    default exports (`import User from` a module whose default is `class UserEntity`) are followed to the
    definition, a CommonJS `exports.x = <expr>` (also an `Object.assign(module.exports, { x: <expr> })` key) and
    a destructured `export const { x } = ...` being x's own definition at that line, `exports.x =
    catchAsync(x)` the module's own x. The edge then binds in the module that defines the name
    (TypedEdge.scope "exact:" with the definition's line; never the importing file's own symbol of that name;
    where the module defines the name at two lines — `exports.x = ...` beside a `function x` — only a node at
    that line), so a monorepo's many `AppService` / `CatsService` classes do not fan out
    AMBIGUOUS. A name imported from a package the tree does not hold (`@nestjs/*`, `typeorm`, `express`, a
    node builtin, `@vuex-orm/core`) binds nothing — it would otherwise bind a same-named repo symbol
    (`AuthGuard` from @nestjs/passport, `Repository` from typeorm); one the tree defines but its module cannot
    be read keeps that module first ("pref:", then the global name rule, never the importing file's own
    symbol), and an alias no config maps is a guess that never narrows several candidates to one;
  * a class named like a runtime or DOM global (`Event`, `File`, `Request`) is a repo class when the file
    imports or declares it, and the global (no edge) otherwise;
  * name resolution can DROP an edge (unresolvable target), never fabricate one; a name resolving to >1
    symbol grades AMBIGUOUS; a handler named by a variable or parameter (not a function of this file, not an
    import) names no symbol and yields nothing;
  * `.d.ts` declaration files, minified / bundled files (sync's vendor suffixes; a 2000+ char line in a file
    whose lines average 200+ chars) and compiled JavaScript (a tsc / Babel / bundler runtime marker, a
    `//# sourceMappingURL=` trailer: a copy of the source's classes) are skipped.

Pure: recognize(source_text, rel_path) only — no fs, no network; the registry's shared walker owns file I/O,
ignore dirs (language_recognizers._TS_IGNORE: node_modules/, dist/, the JS tool dirs; build/ out/ bin/ obj/
target/ tmp/ log/ only in the root or a package root) and per-file error isolation. finalize(root, edges,
exclude=None, files=None) is the cross-file post-pass (router mounts in another module, each NestJS app's
global prefix, the Next.js project root, imports placed in the tree, one handler routed more than once) and
reads the tree itself through the caller's exclusion rule (the registry hands it its walk's file list).
"""
from __future__ import annotations

import bisect
import json
import posixpath
import re
import sys as _sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in _sys.path:
    _sys.path.insert(0, str(_HERE))

from framework_edges import TypedEdge  # noqa: E402  (the shared edge shape; endpoints name+file)

# ---- lexer ---------------------------------------------------------------------------------------
_INTEREST = re.compile(r"[/'\"`()]")
_INTEREST_EXPR = re.compile(r"[/'\"`(){}]")          # inside a template's ${ } braces count too
_SQ = re.compile(r"'((?:[^'\\\n]|\\[\s\S])*)(')?")
_DQ = re.compile(r'"((?:[^"\\\n]|\\[\s\S])*)(")?')
_TPL_BODY = re.compile(r"(?:[^`\\$]|\\[\s\S]|\$(?!\{))*")
_REGEX_LIT = re.compile(r"/(?:[^/\\\[\n]|\\[^\n]|\[(?:[^\]\\\n]|\\[^\n])*\])+(/)[A-Za-z]*")
_WORD_END = re.compile(r"[A-Za-z_$][\w$]*\Z")
_NOT_NL = re.compile(r"[^\n]")
_UNESCAPE = re.compile(r"\\([\s\S])")
# A `/` after one of these (or at the start) opens a regex literal; after a value it is a division.
_REGEX_AFTER = frozenset("(,=:[!&|?{};+-*%<>~^}")
_REGEX_AFTER_WORDS = frozenset({"return", "typeof", "case", "do", "else", "in", "of", "new", "delete",
                                "void", "throw", "instanceof", "yield", "await"})
# A statement may follow the `)` of these heads, so a `/` right after it opens a regex literal.
_HEAD_WORDS = frozenset({"if", "while", "for", "with"})
_JSX_EXTS = (".tsx", ".jsx", ".js")                    # files whose text may hold JSX
_BRACKET = re.compile(r"[()\[\]{}]")
_PAIR = {")": "(", "]": "[", "}": "{"}
_WS_RUN = re.compile(r"[ \t\r\n\f\v]*")
_WS_CHARS = " \t\r\n\f\v"
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_DOTTED = re.compile(r"[A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)*")
_ID_CHARS = re.compile(r"[\w$]")


def _unescape(s: str) -> str:
    return _UNESCAPE.sub(lambda m: m.group(1), s) if "\\" in s else s


# A JSX tag ending a line's text (`<p>`, `<a href="x">`, `<b onClick={() => go()}>`, `<p className={a > b ? x :
# y}>`, `</b>`, `<br />`; an attribute expression may hold `>` and nest braces three deep), and the plain text that
# may follow it before a backtick: words, spaces, `.` `!` `#` `@`, any non-ASCII — no operator, bracket, quote,
# separator or `;`, so a template after `n > 0 ?` or in `f(a > b, ...)` is never text.
_JSX_TAG = re.compile(r"</?[A-Za-z][\w.:-]*(?:\s(?:=>|\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\}|[^<>{}])*)?>")
_JSX_TEXT = re.compile(r"(?:[\w \t.!#@]|[^\x00-\x7f])*")
_JSX_SCAN = 1000               # characters the JSX-text rule reads on either side of a backtick


def _jsx_text_tick(src: str, s: int) -> bool:
    """The backtick at `s` is JSX text, not a template: the last `>` before it on its line closes a JSX tag (an
    opening tag not right after a value: `a<b>` is a comparison or a type argument), only plain text lies
    between that tag and the backtick (_JSX_TEXT), and a closing tag `</` follows it on the line with no other
    backtick in between (`<p>Wrap code in ` marks.</p>`). Reads at most _JSX_SCAN characters on either side, so a
    long line costs linear time."""
    lo = max(0, s - _JSX_SCAN)
    ls = src.rfind("\n", lo, s) + 1 or lo
    gt = src.rfind(">", ls, s)
    if gt < 0 or not _JSX_TEXT.fullmatch(src, gt + 1, s):
        return False
    lt = src.rfind("<", ls, gt)
    if lt < 0 or not _JSX_TAG.fullmatch(src, lt, gt + 1):
        return False
    if src[lt + 1] != "/" and lt > ls and (src[lt - 1].isalnum() or src[lt - 1] in "_$.)]"):
        return False
    hi = min(len(src), s + 1 + _JSX_SCAN)
    le = src.find("\n", s + 1, hi)
    k = src.find("</", s + 1, hi if le < 0 else le)
    return k >= 0 and src.find("`", s + 1, k) < 0


def _lex(src: str, jsx: bool = False) -> tuple:
    """(code, lits): `code` is `src` with every comment, string / template body and regex body replaced by
    spaces (newlines kept, so offsets and line numbers are unchanged; quotes stay), and `lits` maps the
    offset of each string / template opening quote to its value — None for a template with ${ } or an
    unterminated string (neither is a plain literal). A `/` opens a regex literal where JavaScript reads one:
    at the start, after an operator or a keyword such as `return`, and after the `)` of an if / while / for /
    with head — never after `x++` / `x--`, nor right after `<` (`</b>`, a closing tag). With `jsx` (a .tsx /
    .jsx / .js file) a backtick in a line of JSX text is text, not a template (_jsx_text_tick). Linear; never
    raises on malformed input."""
    n = len(src)
    blanks: list = []
    lits: dict = {}
    pos = 0
    last, word = "", ""
    head = False                       # the last token is the `)` of an if / while / for / with head
    parens: list = []                  # per open `(`: does it open such a head?
    exprs: list = []                   # brace depth inside each open ${ } (innermost last)
    tpls: list = []                    # [opening offset, has ${ }] per open template (outermost first)
    in_body = False
    while pos < n:
        if in_body:
            e = _TPL_BODY.match(src, pos).end()
            if e >= n:                                   # unterminated template: blank to the end
                blanks.append((tpls[0][0] + 1, n))
                for start, _dyn in tpls:
                    lits[start] = None
                tpls.clear()
                exprs.clear()
                break
            if src[e] == "`":
                start, dyn = tpls.pop()
                lits[start] = None if dyn else _unescape(src[start + 1:e])
                if not tpls:
                    blanks.append((start + 1, e))
                pos = e + 1
                last, word, head = "`", "", False
                in_body = False
                continue
            tpls[-1][1] = True                           # "${": a dynamic template
            exprs.append(0)
            pos = e + 2
            last, word, head = "{", "", False
            in_body = False
            continue
        m = (_INTEREST_EXPR if exprs else _INTEREST).search(src, pos)
        if m is None:
            break
        s = m.start()
        if s > pos:
            t = src[pos:s].rstrip()
            if t:
                last = t[-1]
                wm = _WORD_END.search(t[-64:])
                word = wm.group(0) if wm else ""
                if word and t[-len(word) - 1:-len(word)] == ".":
                    word = "."                           # a member named like a keyword (`obj.if`)
                elif last in "+-" and t[-2:] in ("++", "--"):
                    last = "a"                           # `i++ / 2`: a postfix operator ends a value
                head = False
        c = src[s]
        if c == "(":
            parens.append(word in _HEAD_WORDS)
            pos = s + 1
            last, word, head = "(", "", False
            continue
        if c == ")":
            was_head = parens.pop() if parens else False
            pos = s + 1
            last, word, head = ")", "", was_head
            continue
        if c == "/":
            nxt = src[s + 1] if s + 1 < n else ""
            if nxt == "/":
                e = src.find("\n", s)
                e = n if e < 0 else e
                blanks.append((s, e))
                pos = e
                continue
            if nxt == "*":
                e = src.find("*/", s + 2)
                e = n if e < 0 else e + 2
                blanks.append((s, e))
                pos = e
                continue
            if (last == "" or last in _REGEX_AFTER or word in _REGEX_AFTER_WORDS or head) \
                    and src[s - 1:s] != "<":             # `</b>` is a closing tag, never a regex literal
                rm = _REGEX_LIT.match(src, s)
                if rm:
                    blanks.append((s + 1, rm.start(1)))  # the body only: slashes + flags keep the span's extent
                    pos = rm.end()
                    last, word, head = "a", "", False
                    continue
            pos = s + 1
            last, word, head = "/", "", False
            continue
        if c == "'" or c == '"':
            sm = (_SQ if c == "'" else _DQ).match(src, s)
            e = sm.end()
            closed = sm.group(2) is not None
            lits[s] = _unescape(sm.group(1)) if closed else None
            blanks.append((s + 1, e - 1 if closed else e))
            pos = e
            last, word, head = c, "", False
            continue
        if c == "`":
            if jsx and _jsx_text_tick(src, s):              # a backtick in JSX text is text
                pos = s + 1
                last, word, head = "a", "", False
                continue
            tpls.append([s, False])
            pos = s + 1
            in_body = True
            continue
        if c == "{":
            exprs[-1] += 1
        elif exprs[-1] == 0:                             # "}" closing a template's ${ }
            exprs.pop()
            pos = s + 1
            in_body = True
            continue
        else:
            exprs[-1] -= 1
        pos = s + 1
        last, word, head = c, "", False
    if not blanks:
        return src, lits
    blanks.sort()
    out: list = []
    cur = 0
    for a, b in blanks:
        if b <= cur:
            continue
        a = max(a, cur)
        out.append(src[cur:a])
        out.append(_NOT_NL.sub(" ", src[a:b]))
        cur = b
    out.append(src[cur:])
    return "".join(out), lits


def _brackets(code: str) -> dict:
    """{offset of a bracket: offset of its partner} for the (), [], {} of `code` (strings/comments already
    blanked). A closer with no opener within 8 levels is ignored; an unclosed opener has no entry. O(n)."""
    match: dict = {}
    stack: list = []
    for m in _BRACKET.finditer(code):
        c, i = m.group(0), m.start()
        if c in "([{":
            stack.append((c, i))
            continue
        want = _PAIR[c]
        k = len(stack) - 1
        floor = max(-1, k - 8)
        while k > floor and stack[k][0] != want:
            k -= 1
        if k <= floor:
            continue
        del stack[k + 1:]
        o = stack.pop()[1]
        match[o] = i
        match[i] = o
    return match


# ---- vocabulary -----------------------------------------------------------------------------------
_HTTP_DECOS = {"Get": "GET", "Post": "POST", "Put": "PUT", "Patch": "PATCH", "Delete": "DELETE",
               "All": "*", "Options": "OPTIONS", "Head": "HEAD", "Sse": "GET", "Search": "SEARCH"}
_NEST_ONLY_HTTP = {"Sse", "Search"}     # NestJS-only names: a route only when imported from @nestjs/*
# The `prefix_unresolved` of a route declared on a class with no @Controller: Nest serves it only under the
# prefix of each subclass (an abstract CRUD base), which this class cannot know.
_NO_CONTROLLER = "<no @Controller: the prefix of each subclass>"
_CONTROLLER_DECOS = {"Controller", "JsonController"}
_DI_CLASS_DECOS = {"Injectable", "Controller", "JsonController", "Module", "Resolver", "WebSocketGateway",
                   "Catch", "Processor", "Component", "Directive", "Pipe", "NgModule", "Service",
                   "injectable", "singleton", "scoped", "autoInjectable"}     # + tsyringe / inversify
_SCHED_DECOS = {"Cron": "cron", "Interval": "interval", "Timeout": "timeout"}
_EVENT_DECOS = {"OnEvent": None, "EventPattern": "microservice", "MessagePattern": "microservice",
                "SubscribeMessage": "websocket"}
_HOOK_DECOS = {"UseGuards": "guard", "UseInterceptors": "interceptor", "UsePipes": "pipe",
               "UseFilters": "filter"}
_REL_DECOS = {"OneToMany": "HAS_MANY", "ManyToMany": "HAS_MANY", "ManyToOne": "REFERENCES",
              "OneToOne": "REFERENCES"}
_ENTITY_DECOS = {"Entity", "ViewEntity", "ChildEntity"}
# TypeORM / MikroORM entity listeners: a Rails before_save callback's shape (HOOKS class -> its own method).
_LISTENER_DECOS = {"BeforeInsert", "AfterInsert", "BeforeUpdate", "AfterUpdate", "BeforeRemove", "AfterRemove",
                   "BeforeSoftRemove", "AfterSoftRemove", "BeforeRecover", "AfterRecover", "AfterLoad",
                   "BeforeCreate", "AfterCreate", "BeforeDelete", "AfterDelete", "BeforeUpsert", "AfterUpsert",
                   "OnInit", "OnLoad"}
_INJECT_ENTITY_DECOS = {"InjectRepository", "InjectModel"}
_ACCESS_WORDS = {"public", "private", "protected", "readonly", "override"}
_MEMBER_MODIFIERS = {"public", "private", "protected", "readonly", "static", "async", "abstract",
                     "override", "declare", "accessor", "get", "set"}
# Router verbs. `del` is koa-router's alias for DELETE; `all` matches every method.
_ROUTE_VERBS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "delete": "DELETE",
                "del": "DELETE", "all": "*", "head": "HEAD", "options": "OPTIONS"}
_NEXT_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
_NEXT_FILE_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_NEXT_CONFIGS = {"next.config.js", "next.config.mjs", "next.config.cjs", "next.config.ts", "next.config.mts"}
# TypeScript's NodeNext resolution: an import names the COMPILED file (`./users.js`) of a source (users.ts).
_TS_TWINS = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}
_TEST_DIR_PARTS = {"test", "tests", "__tests__", "spec", "specs", "testing", "e2e", "__mocks__"}
# Module specifiers of the HTTP frameworks whose router calls this module reads.
_FRAMEWORK_SPECS = {"express": "express", "fastify": "fastify", "koa": "koa", "@koa/router": "koa",
                    "koa-router": "koa", "hono": "hono", "polka": "express", "router": "express"}
_CONVENTIONAL_RECEIVERS = {"app", "router", "server", "fastify", "instance"}
# App / router types a framework module exports: a name annotated with one is a receiver whatever it is called.
_ROUTER_TYPE_NAMES = {"Express", "Application", "Router", "IRouter", "FastifyInstance", "Hono"}
_ROUTER_CLASS_SPECS = {"@koa/router", "koa-router"}     # their default export is the Router class itself
# Wrapped final handlers (`asyncHandler(fn)`, `catchAsync(ctrl.list)`, `withAuth(h)`) are unwrapped one level.
_WRAPPER_NAME = re.compile(r"(?:with[A-Z][\w$]*|[\w$]*[Aa]sync[\w$]*|[\w$]*[Cc]atch[\w$]*|[\w$]*[Ww]rap[\w$]*|"
                           r"[\w$]*[Hh]andler[\w$]*|safe[\w$]*|try[A-Z][\w$]*)\Z")
# Wrappers that exist to make a request handler (a property they wrap is one), and a handler's parameter names.
_HANDLER_WRAPPERS = {"asyncHandler", "expressAsyncHandler", "catchAsync", "catchAsyncErrors", "asyncWrapper",
                     "wrapAsync", "asyncMiddleware", "asyncRoute", "catchErrors", "handleAsync", "withAsyncHandler"}
_HANDLER_PARAMS = {"req", "request", "res", "response", "reply", "ctx", "context", "c", "next"}
# Scalars and runtime / DOM globals: never an injected dependency or an entity reference, unless the file
# imports or declares a class of that name (_class_target).
_TS_NON_ENTITY = {
    "String", "Number", "Boolean", "Object", "Symbol", "BigInt", "Function", "Date", "RegExp", "Error",
    "Promise", "Array", "ReadonlyArray", "Map", "Set", "WeakMap", "WeakSet", "Record", "Partial",
    "Required", "Readonly", "Pick", "Omit", "Exclude", "Extract", "NonNullable", "ReturnType",
    "InstanceType", "Parameters", "Awaited", "Uint8Array", "ArrayBuffer", "Buffer", "JSON", "Math",
    "Window", "Document", "HTMLElement", "Element", "Node", "Event", "Request", "Response", "Headers",
    "URL", "URLSearchParams", "Blob", "File", "FormData", "Iterable", "Iterator", "AsyncIterable",
    "Generator", "PromiseLike", "ArrayLike", "Console", "Intl", "Proxy", "Reflect", "TemplateStringsArray",
}
# Relation property wrappers whose type argument is the related entity (MikroORM's reference types included).
_REL_WRAPPERS = {"Array", "Promise", "Relation", "Collection", "Ref", "Reference", "Rel", "Lazy", "Loaded",
                 "LazyRef", "IdentifiedReference", "EntityRef"}
# Scopes of third-party npm packages: a name imported from one is never repo code (is_package). Any other
# specifier that names no file of the tree is a package too, which finalize decides; to the router reader
# (is_external) an unscoped bare specifier always is one.
_THIRD_PARTY_SCOPES = {
    "nestjs", "angular", "types", "prisma", "aws-sdk", "aws-cdk", "google-cloud", "azure", "apollo", "mui",
    "material-ui", "reduxjs", "tanstack", "vue", "babel", "testing-library", "sentry", "opentelemetry",
    "grpc", "fastify", "koa", "hono", "mikro-orm", "typegoose", "golevelup", "ngrx", "ionic", "capacitor",
    "firebase", "supabase", "trpc", "nrwl", "nx", "storybook", "emotion", "chakra-ui", "headlessui",
    "radix-ui", "vitejs", "sveltejs", "nuxt", "remix-run", "vercel", "next", "tensorflow", "octokit",
    "slack", "sendgrid", "stripe", "auth0", "clerk", "hapi", "graphql-tools", "casl", "discordjs",
    "faker-js", "jest", "swc", "typescript-eslint", "playwright", "sinonjs", "elastic", "temporalio",
    "socket.io", "nestjs-modules", "ngx-translate", "fortawesome", "angular-devkit", "schematics",
    "microsoft", "azure-rest", "smithy", "middy", "aws-lambda-powertools", "datadog", "launchdarkly",
    "segment", "upstash", "planetscale", "neondatabase", "drizzle-team", "graphql-codegen", "envelop",
    "pothos", "nestjs-cls", "bull-board", "anatine", "automapper", "liaoliaots", "songkeys", "ntegral",
    "lit", "builder.io", "tiptap", "uppy", "uiw", "dnd-kit", "floating-ui", "heroicons", "react-native",
    "expo", "shopify", "hookform", "mantine", "nextui-org", "tailwindcss", "trivago", "commitlint",
}
# Specifier prefixes read as a path alias. `@app/` is not one: it is the Nest CLI's LIBRARY alias (`@app/common`
# -> libs/common/src), so its tail is no path suffix; finalize resolves it through the tsconfig `paths`.
_ALIAS_PREFIXES = ("@/", "~/", "~", "#", "src/", "app/", "@src/")
_MINIFIED_SUFFIXES = (".min.js", ".bundle.js", "-min.js", ".min.mjs")
_DECLARATION_SUFFIXES = (".d.ts", ".d.mts", ".d.cts")
# Compiled JavaScript (tsc / Babel / a bundler) is a second copy of code that lives in its source: the markers
# graphify_extract._looks_generated reads (mirrored as text, drift-locked by tests/typescript_edges_gates.py),
# looked for in a .js / .mjs / .cjs / .jsx file's head, plus a `//# sourceMappingURL=` trailer.
_COMPILED_JS_EXTS = (".js", ".mjs", ".cjs", ".jsx")
_COMPILED_JS_MARKERS = (
    "__webpack_require__", "webpackBootstrap", "__webpack_modules__", "__nccwpck_require__",
    "parcelRequire", "System.register(", "__toESM(", "__toCommonJS(", "__commonJS(",
    'Object.defineProperty(exports, "__esModule"', "Object.defineProperty(exports, '__esModule'",
    "(this && this.__", "_interopRequireDefault(", "typeof define === 'function' && define.amd",
    'typeof define === "function" && define.amd',
)
_COMPILED_HEAD = 16384
_MINIFIED_LINE = 2000          # a line this long is bundler/minifier output (graphify_extract's rule) ...
_MINIFIED_AVG = 200            # ... when the file's lines average this long too
_MAX_HEADER = 4000             # chars scanned for a class header / an object key before giving up
_MAX_PATHS = 32                # composed paths kept per route
_MAX_MOUNT_DEPTH = 12          # router mount chains followed in finalize
_MAX_IMPORT_HOPS = 6
_MAX_LOOKUP_DEPTH = 32         # re-export hops finalize follows (cycles stop at once; this bounds the recursion)
_MAX_HERITAGE = 8              # `extends` hops followed for an inherited member (a cycle stops here)


def is_external(spec) -> bool:
    """The router reader's rule: a module specifier that names a third-party package or a runtime builtin
    (any unscoped bare name), never this repo's code."""
    s = str(spec or "")
    if not s or s.startswith((".", "/")):
        return False
    if s.startswith("node:"):
        return True
    if s.startswith(_ALIAS_PREFIXES):
        return False
    if s.startswith("@"):
        return s[1:].split("/", 1)[0] in _THIRD_PARTY_SCOPES
    return True


def is_package(spec) -> bool:
    """A specifier that names a third-party package or a runtime builtin whatever the tree holds: `node:x` or a
    known third-party @scope. A type, guard or handler imported from one never binds a repo symbol. Any other
    non-relative specifier — `typeorm`, `events`, but also `domain/user.entity` under a tsconfig `baseUrl` — is
    resolved against the tree by finalize, the way TypeScript does (paths, baseUrl, then packages)."""
    s = str(spec or "")
    if s.startswith("node:"):
        return True
    return s.startswith("@") and s[1:].split("/", 1)[0] in _THIRD_PARTY_SCOPES


# ---- one lexed file -------------------------------------------------------------------------------
# Whole-file patterns start with their LITERAL keyword and check the boundary right after it
# (`import(?<![\w$.]import)`): a pattern that starts with a lookbehind cannot use the regex engine's fast
# literal search and tries every offset (~10x slower on real code).
_DECL_KW = r"(?:const(?<![\w$.]const)|let(?<![\w$.]let)|var(?<![\w$.]var))"
_IMPORT_FROM = re.compile(r"import(?<![\w$.]import)\s+(?:type\s+)?([\w$\s{},*]{0,4000}?)\s*\bfrom\s*(['\"])")
_IMPORT_EQ = re.compile(r"import(?<![\w$.]import)\s+([A-Za-z_$][\w$]*)\s*=\s*require\s*\(\s*(['\"])")
_REQUIRE_VAR = re.compile(_DECL_KW + r"\s+([A-Za-z_$][\w$]*)\s*=\s*require\s*\(\s*(['\"])")
_REQUIRE_DESTR = re.compile(_DECL_KW + r"\s*\{([^{}]{0,2000})\}\s*=\s*require\s*\(\s*(['\"])")
_FUNC_DECL = re.compile(r"function(?<![\w$.]function)\s*\*?\s*([A-Za-z_$][\w$]*)\s*[<(]")
_VAR_DECL = re.compile(_DECL_KW + r"\s+([A-Za-z_$][\w$]*)\s*(?::\s*[\w$.<>\[\], |]{0,200})?=(?!=)\s*")
_DECL_HEAD = re.compile(_DECL_KW + r"(?=[\s{\[])")     # a const / let / var keyword before its declarators
_THIS_ASSIGN = re.compile(r"this(?<![\w$]this)\s*\.\s*([A-Za-z_$][\w$]*)\s*=(?!=)\s*")
_CLASS_KW = re.compile(r"class(?<![\w$.#@]class)\s+([A-Za-z_$][\w$]*)")
_IFACE_KW = re.compile(r"interface(?<![\w$.#@]interface)\s+([A-Za-z_$][\w$]*)")
_ARROW_HEAD = re.compile(r"(?:async\s*)?(?:\(|[A-Za-z_$][\w$]*\s*=>)")
_FN_HEAD = re.compile(r"(?:async\s+)?function\b")
_EXPORT_DEFAULT = re.compile(r"export(?<![\w$.]export)\s+default\s+")
_EXPORT_NAMED_DECL = re.compile(
    r"export(?<![\w$.]export)\s+(?:declare\s+)?(?:abstract\s+)?(?:async\s+)?(?:const|let|var|function\s*\*?|class)\s+"
    r"([A-Za-z_$][\w$]*)")
_EXPORT_LIST = re.compile(r"export(?<![\w$.]export)\s*\{([^{}]{0,4000})\}(?!\s*from)")
_MODULE_EXPORTS = re.compile(r"module(?<![\w$.]module)\s*\.\s*exports\s*=(?!=)\s*")
_CHAINED_EXPORTS = re.compile(r"exports(?![\w$])\s*=(?![=>])\s*")   # `module.exports = exports = ...`
# What follows `require('x')`: `.member` (an import of that member) and / or a call (a value, not an import).
_AFTER_REQUIRE = re.compile(r"(?:\s*\.\s*([A-Za-z_$][\w$]*))?(\s*\()?")
_NEW_RE = re.compile(r"new\s+([A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)*)\s*(?:<[^()]{0,200}?>)?\s*")
_ASYNC_RE = re.compile(r"async\b\s*")
_TYPE_ARGS_CALL = re.compile(r"<[^()]{0,300}?>\s*\(")
_GENERIC_METHOD = re.compile(r"<[^(){};]{0,300}?>\s*\(")
_NUMBER_NAME = re.compile(r"\d[\w.]*")
_ANON_DEFAULT_FN = re.compile(
    r"(?:export(?<![\w$.]export)\s+default|module(?<![\w$.]module)\s*\.\s*exports\s*=(?:\s*exports(?![\w$])\s*=(?![=>]))?)"
    r"\s*(?:async\s+)?"
    r"(?:function\s*\*?\s*\(|(?=\(|async\s*\())")
_EXPORTS_PROP = re.compile(r"(?:module(?<![\w$.]module)\s*\.\s*exports|exports(?<![\w$.]exports))\s*\.\s*"
                           r"([A-Za-z_$][\w$]*)\s*=(?!=)\s*")
_ASSIGN_EXPORTS = re.compile(r"Object(?<![\w$.]Object)\s*\.\s*assign\s*\(\s*"
                             r"(?:module(?<![\w$.]module)\s*\.\s*exports|exports(?<![\w$.]exports))\s*,\s*\{")
_EXPORT_DESTRUCT = re.compile(r"export(?<![\w$.]export)\s+(?:declare\s+)?(?:const|let|var)\s*[{\[]")
_CLASS_EXPR = re.compile(r"class(?![\w$])()")       # a class expression's keyword (its group: no name for _class_at)
_DESTRUCT_DECL = re.compile(_DECL_KW + r"\s*[{\[]")
_ARROW = re.compile(r"=>")
# `this.<field>` written by an assignment (`=`, `+=`, `??=`, ...), never a comparison.
_THIS_WRITE = re.compile(r"this(?<![\w$]this)\s*\.\s*([A-Za-z_$][\w$]*)\s*"
                         r"(?:\*\*|<<|>>>?|&&|\|\||\?\?|[-+*/%&|^])?=(?![=>])")
# A `(` preceded by one of these opens a statement head or an expression, never a parameter list.
_NOT_PARAM_WORDS = frozenset({"if", "for", "while", "switch", "with", "return", "typeof", "await", "yield",
                              "in", "of", "new", "delete", "void", "throw", "case", "do", "else", "instanceof"})


def _is_param_list(f: "_File", o: int, c: int) -> bool:
    """The `(` ... `)` at o .. c is a function's parameter list or a catch clause: an arrow's `(...) =>`, or a
    `function` / method / `catch` head followed by its body (`{`) or a return type that leads to one (or to
    `=>`) before any `;`. An if / for / while / switch head or a call is not."""
    code = f.code
    n = len(code)
    j = f.ws(c + 1, n)
    if code.startswith("=>", j):
        return True
    if j >= n or code[j] not in "{:":
        return False
    p = o - 1
    while p >= 0 and code[p] in _WS_CHARS:
        p -= 1
    wm = _WORD_END.search(code, max(0, p - 63), p + 1) if p >= 0 else None
    if wm is not None:
        if wm.group(0) in _NOT_PARAM_WORDS:
            return False
    elif p < 0 or code[p] not in ">*":                  # `name<T>(...)`, `function* (...)`
        return False
    if code[j] == "{":
        return True
    lim = min(n, j + 200)
    for k in range(j + 1, lim):
        ch = code[k]
        if ch == ";":
            return False
        if ch == "{" or code.startswith("=>", k):
            return True
    return False


class _Member:
    __slots__ = ("kind", "name", "pos", "decos", "mods", "params", "type_span", "init_span", "end")

    def __init__(self, kind, name, pos, decos, mods, params, type_span, init_span, end=0):
        self.kind = kind            # "method" | "prop"
        self.name = name            # identifier ('#x' for a private name) or None (string / computed)
        self.pos = pos
        self.decos = decos          # [(name, open, close)] in source order
        self.mods = mods
        self.params = params        # (open, close) of a method's parameter list
        self.type_span = type_span  # (a, b) of a property's type annotation
        self.init_span = init_span  # (a, b) of a property's initializer
        self.end = end              # where the member (a method's body included) ends


class _Class:
    __slots__ = ("name", "kw", "body", "end", "decos", "is_expr", "top", "extends", "implements",
                 "members", "facts", "iface")

    def __init__(self, name, kw, body, end, decos, is_expr, top, iface=False):
        self.name, self.kw, self.body, self.end = name, kw, body, end
        self.decos, self.is_expr, self.top, self.iface = decos, is_expr, top, iface
        self.extends: list = []       # [(bare name, raw dotted text)]
        self.implements: list = []
        self.members: list = []
        self.facts: list = []


class _File:
    """One lexed TS/JS file plus the module-level facts every reader shares."""

    def __init__(self, source: str, rel: str):
        self.src = source
        self.rel = rel
        self.stem = Path(rel).stem
        low = rel.lower()
        self.ts = low.endswith((".ts", ".tsx"))
        self.code, self.lits = _lex(source, jsx=low.endswith(_JSX_EXTS))
        self.match = _brackets(self.code)
        self.nl = [m.start() for m in re.finditer("\n", source)]
        self.imports: dict = {}      # local -> (specifier, imported: "default" | "*" | "=" | name)
        self.funcs: dict = {}        # function-like declarations of this file: name -> offset
        self.func_decls: dict = {}   # name -> the offset of every function-like declaration of that name
        self.vars: dict = {}         # other variable bindings: name -> (initializer start, end)
        self.declared: dict = {}     # name -> how many const / let / var / for-of / for-in declarators bind it
        self.const_inits: dict = {}  # const name -> (initializer start, end, statement offset): its value
        self.new_vars: dict = {}     # x = new X(...) -> X
        self.classes: dict = {}      # class name -> _Class (declarations at any depth)
        self.exports: dict = {}      # export name ("default" for the default export) -> local name
        self.prop_defs: dict = {}    # export name -> offset of its own definition (`exports.x = <expr>`, a
                                     # `module.exports = { x() {} }` member, `export const { x } = ...`)
        self.wrapped: dict = {}      # CommonJS export name -> (name, offset): `exports.x = catchAsync(name)`
        self.cjs_keys: set = set()   # names CommonJS sets on module.exports (`exports.x =`, `module.exports = { x }`)
        self.field_classes: dict = {}  # (class body, field) -> the class of `this.<field>` (_field_class)
        self.written_fields: dict = {}  # class body -> the fields its code assigns (`this.x = ...`)
        self._bound = None           # names parameters / catch clauses / destructuring bind (bound_names)
        self._parents = None         # enclosing `{` of each brace pair (block_of)
        self.class_index = None      # class bodies sorted, with their enclosing body (_enclosing_class)
        opens = sorted(o for o, c in self.match.items() if o < c and self.code[o] == "{")
        self.brace_opens = opens
        self.brace_closes = sorted(self.match[o] for o in opens)
        self._scan_imports()
        self._scan_decls()
        self._scan_exports()

    # -- small helpers -------------------------------------------------------------------------------
    def line(self, pos: int) -> int:
        return bisect.bisect_left(self.nl, pos) + 1

    def ws(self, i: int, end: int) -> int:
        return min(_WS_RUN.match(self.code, i).end(), end) if i < end else i

    def lit(self, pos: int):
        """The value of the string literal whose opening quote is at `pos` (None: not a plain literal)."""
        return self.lits.get(pos)

    def depth(self, pos: int) -> int:
        """Brace depth at `pos` (matched braces only)."""
        return bisect.bisect_left(self.brace_opens, pos) - bisect.bisect_left(self.brace_closes, pos)

    def close_of(self, i: int, end: int) -> int:
        """The partner of the bracket at `i`, else `end` (an unclosed group runs to the end)."""
        j = self.match.get(i)
        return j if (j is not None and j <= end) else end

    def block_of(self, pos: int):
        """(open, close) of the innermost `{ }` around `pos`, or None at module level. The enclosing brace of
        every pair is computed once (linear); a query walks up from the last `{` before `pos`."""
        opens = self.brace_opens
        if self._parents is None:
            parents, stack = [], []
            for i, o in enumerate(opens):
                while stack and self.match[opens[stack[-1]]] < o:
                    stack.pop()
                parents.append(stack[-1] if stack else -1)
                stack.append(i)
            self._parents = parents
        i = bisect.bisect_left(opens, pos) - 1
        while i >= 0:
            if self.match[opens[i]] > pos:
                return opens[i], self.match[opens[i]]
            i = self._parents[i]
        return None

    def bound_names(self) -> set:
        """Every name a parameter list, a catch clause, a one-parameter arrow (`x => ...`) or a destructuring
        declaration binds anywhere in the file: a `const` of such a name may be shadowed where it is read.
        Each source range is read once (nested lists sit inside their outer one), so this is linear."""
        if self._bound is None:
            code = self.code
            spans = [(o + 1, c) for o, c in self.match.items()
                     if o < c and code[o] == "(" and _is_param_list(self, o, c)]
            for m in _DESTRUCT_DECL.finditer(code):
                o = m.end() - 1
                c = self.match.get(o)
                if c is not None and c > o:
                    spans.append((o + 1, c))
            got: set = set()
            for m in (_ARROW.finditer(code) if "=>" in code else ()):
                k = m.start() - 1
                while k >= 0 and code[k] in _WS_CHARS:
                    k -= 1
                wm = _WORD_END.search(code, max(0, k - 63), k + 1)
                if wm:
                    got.add(wm.group(0))
            end = 0
            for a, b in sorted(spans):
                a = max(a, end)
                if a < b:
                    got.update(_IDENT.findall(code, a, b))
                    end = b
            self._bound = got
        return self._bound

    def fn_visible(self, name: str, pos) -> bool:
        """A function of this file named `name` is in scope at `pos` (None: at module level): one declared at
        module level, or in a block that holds `pos`. A helper nested in another function is not."""
        for d in self.func_decls.get(name, ()):
            blk = self.block_of(d)
            if blk is None or (pos is not None and blk[0] < pos < blk[1]):
                return True
        return False

    def import_spec(self, name: str):
        imp = self.imports.get(name)
        return imp[0] if imp else None

    # -- module-level scans --------------------------------------------------------------------------
    def _bind_import(self, local, spec, imported):
        if local and spec is not None and local not in self.imports:
            self.imports[local] = (spec, imported)

    def _scan_imports(self):
        code = self.code
        for m in _IMPORT_FROM.finditer(code):
            spec = self.lit(m.start(2))
            if spec is None:
                continue
            clause = m.group(1).strip()
            brace = re.search(r"\{([^{}]*)\}", clause)
            head = clause[:brace.start()] if brace else clause
            for part in head.split(","):
                part = part.strip()
                if not part:
                    continue
                sm = re.match(r"\*\s*as\s+([A-Za-z_$][\w$]*)$", part)
                if sm:
                    self._bind_import(sm.group(1), spec, "*")
                elif re.fullmatch(r"(?:type\s+)?[A-Za-z_$][\w$]*", part):
                    self._bind_import(part.split()[-1], spec, "default")
            if brace:
                for item in brace.group(1).split(","):
                    toks = item.replace("type ", " ").split()
                    if not toks:
                        continue
                    if len(toks) >= 3 and toks[1] == "as":
                        self._bind_import(toks[2], spec, toks[0])
                    elif re.fullmatch(r"[A-Za-z_$][\w$]*", toks[0]):
                        self._bind_import(toks[0], spec, toks[0])
        if "require" not in code:
            return
        for m in _IMPORT_EQ.finditer(code):
            spec = self.lit(m.start(2))
            if spec is not None:
                self._bind_import(m.group(1), spec, "=")
        for m in _REQUIRE_VAR.finditer(code):
            spec = self.lit(m.start(2))
            if spec is None:
                continue
            close = self.close_of(code.rfind("(", m.start(), m.start(2)), len(code))
            mm = _AFTER_REQUIRE.match(code, close + 1)
            if mm.group(2) is not None:
                continue                                 # require('x')() / require('x').Router(): a value
            self._bind_import(m.group(1), spec, mm.group(1) or "=")
        for m in _REQUIRE_DESTR.finditer(code):
            spec = self.lit(m.start(2))
            if spec is None:
                continue
            for item in m.group(1).split(","):
                toks = [t for t in re.split(r"\s*:\s*", item.strip()) if t]
                if len(toks) == 1 and _IDENT.fullmatch(toks[0]):
                    self._bind_import(toks[0], spec, toks[0])
                elif len(toks) == 2 and _IDENT.fullmatch(toks[0]) and _IDENT.fullmatch(toks[1]):
                    self._bind_import(toks[1], spec, toks[0])

    def _scan_decls(self):
        code = self.code
        n = len(code)
        for m in _FUNC_DECL.finditer(code):
            self.funcs.setdefault(m.group(1), m.start(1))
            self.func_decls.setdefault(m.group(1), []).append(m.start(1))
        self._scan_declarators()
        for m in _VAR_DECL.finditer(code):
            name, i = m.group(1), m.end()
            if name in self.imports:
                continue
            if _FN_HEAD.match(code, i) or _is_arrow(self, i, n):
                self.funcs.setdefault(name, m.start(1))
                self.func_decls.setdefault(name, []).append(m.start(1))
                continue
            nm = re.match(r"new\s+([A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)*)\s*[(<]", code[i:i + 300])
            if nm:
                self.new_vars.setdefault(name, re.sub(r"\s+", "", nm.group(1)))
            self.vars.setdefault(name, (i, _expr_end(self, i, n)))
        for m in _CLASS_KW.finditer(code):
            if m.group(1) in ("extends", "implements"):
                continue
            c = _class_at(self, m)
            if c is not None and c.name not in self.classes:
                self.classes[c.name] = c
        if self.ts and "interface" in code:
            for m in _IFACE_KW.finditer(code):
                c = _class_at(self, m, iface=True)
                if c is not None and c.name not in self.classes:
                    self.classes[c.name] = c

    def _scan_declarators(self):
        """`declared` counts every binding of a const / let / var statement or a for-of / for-in head — with or
        without an initializer, each declarator of a list, a destructuring pattern's names — and `const_inits`
        keeps each const's own initializer: a path fold reads a name only one declaration binds. A `<` in an
        initializer reads as a type argument, so a declarator after a comparison (`a = x < y, b = 1`) is not
        seen."""
        code = self.code
        n = len(code)
        for m in _DECL_HEAD.finditer(code):
            const = code.startswith("const", m.start())
            for s, e in _split(self, m.end(), _expr_end(self, m.end(), n), angles=True):
                if code[s] in "{[":
                    for name in _IDENT.findall(code, s + 1, self.close_of(s, e)):
                        self.declared[name] = self.declared.get(name, 0) + 1
                    continue
                im = _IDENT.match(code, s, e)
                if not im or im.group(0) == "enum":
                    continue
                name = im.group(0)
                self.declared[name] = self.declared.get(name, 0) + 1
                if const and name not in self.const_inits:
                    i = _init_start(self, im.end(), e)
                    if i is not None:
                        self.const_inits[name] = (i, e, m.start())

    def _scan_exports(self):
        code = self.code
        if "export" not in code:
            return
        for m in _EXPORT_DEFAULT.finditer(code):
            rest = code[m.end():m.end() + 400]
            dm = re.match(r"(?:abstract\s+)?(?:async\s+)?(?:function\s*\*?|class)\s+([A-Za-z_$][\w$]*)", rest)
            if dm and dm.group(1) not in ("extends", "implements"):   # `class extends Base`: no name
                self.exports.setdefault("default", dm.group(1))
                continue
            if dm:
                continue
            im = re.match(r"([A-Za-z_$][\w$]*)\s*(?:;|$|\n)", rest)
            if im:
                self.exports.setdefault("default", im.group(1))
        for m in _EXPORT_NAMED_DECL.finditer(code):
            self.exports.setdefault(m.group(1), m.group(1))
        for m in _EXPORT_DESTRUCT.finditer(code):          # `export const { a, b: c } = ...`: defined right there
            for name in _pattern_names(self, m.end() - 1):
                self.prop_defs.setdefault(name, m.start())
        for m in _EXPORT_LIST.finditer(code):
            for item in m.group(1).split(","):
                toks = item.replace("type ", " ").split()
                if len(toks) >= 3 and toks[1] == "as":
                    self.exports.setdefault(toks[2], toks[0])
                elif len(toks) == 1 and _IDENT.fullmatch(toks[0]):
                    self.exports.setdefault(toks[0], toks[0])
        if "exports" not in code:
            return
        for m in _MODULE_EXPORTS.finditer(code):
            e = _cjs_value_start(code, m.end())
            rest = code[e:e + 400]
            im = re.match(r"([A-Za-z_$][\w$]*)\s*(?:;|$|\n)", rest)
            if im:
                self.exports.setdefault("=", im.group(1))
                self.exports.setdefault("default", im.group(1))
                continue
            if rest.startswith("{"):
                self._cjs_object(e)
        if "assign" in code:
            for m in _ASSIGN_EXPORTS.finditer(code):     # Object.assign(module.exports, { ... })
                self._cjs_object(m.end() - 1)
        for m in _EXPORTS_PROP.finditer(code):
            name = m.group(1)
            self.cjs_keys.add(name)
            im = re.match(r"([A-Za-z_$][\w$]*)\s*(?:;|$|\n)", code[m.end():m.end() + 200])
            if im:
                self.exports.setdefault(name, im.group(1))
                continue
            w = _wrapped_name(self, m.end(), _expr_end(self, m.end(), len(code)))
            if w:                                        # exports.x = catchAsync(x): that function, wrapped once
                self.wrapped.setdefault(name, (w, m.start()))
            else:                                        # exports.x = <expr>: the property is its own definition
                self.prop_defs.setdefault(name, m.start())

    def _cjs_object(self, open_: int) -> None:
        """The keys of a `module.exports = { ... }` / Object.assign(module.exports, { ... }) literal are CommonJS
        exports: a name (shorthand or `x: name`), a handler wrapped once (`x: asyncHandler(name)`), or a value
        defined right there, at its key (`x: <expr>`, a method `x() {}`)."""
        for key, mem in _object_members(self, open_).items():
            self.cjs_keys.add(key)
            if mem[0] == "id":
                self.exports.setdefault(key, mem[1])
            elif mem[0] == "wrap":
                self.wrapped.setdefault(key, (mem[1], mem[2]))
            else:
                self.prop_defs.setdefault(key, mem[1])


# ---- scanning helpers -----------------------------------------------------------------------------
def _cjs_value_start(code: str, i: int) -> int:
    """Where the value of a `module.exports =` ending at `i` starts: past a chained `exports =` (the legacy
    `module.exports = exports = { ... }`)."""
    cm = _CHAINED_EXPORTS.match(code, i)
    return cm.end() if cm else i


def _is_arrow(f: _File, i: int, end: int) -> bool:
    """`code[i:]` starts an arrow function: `(params) =>`, `async (...) =>`, `x =>`, `async x =>`."""
    code = f.code
    if not _ARROW_HEAD.match(code, i, end):
        return False
    j = i
    am = _ASYNC_RE.match(code, j, end)
    if am and not code.startswith("=>", am.end()):
        j = am.end()
    if j < end and code[j] == "(":
        j = f.close_of(j, end) + 1
        j = f.ws(j, end)
        if j < end and code[j] == ":":                   # a return type annotation before =>
            k = code.find("=>", j, min(end, j + 400))
            return k >= 0
        return code.startswith("=>", j)
    im = _IDENT.match(code, j)
    if not im:
        return False
    return code.startswith("=>", f.ws(im.end(), end))


def _expr_end(f: _File, i: int, end: int) -> int:
    """The end of the expression starting at `i`: the first `;`, or a newline that ends the statement,
    outside brackets (bounded)."""
    code = f.code
    lim = min(end, i + 20000)
    j = i
    prev = ""                                          # the last non-space char of the expression so far
    while j < lim:
        c = code[j]
        if c in "([{":
            j = f.close_of(j, lim) + 1
            prev = ")"
            continue
        if c in ";)]}":
            return j
        if c == "\n":
            k = f.ws(j, lim)
            if prev and prev not in "=,(:[{|&?.+-*/%<!~^>" and (k >= lim or code[k] not in ".?:|&=+-*/%<>)]`("):
                return j
        elif c not in _WS_CHARS:
            prev = c
        j += 1
    return lim


def _init_start(f: _File, p: int, e: int):
    """Where the initializer of the declarator whose binding ends at `p` starts (past a type annotation and its
    top-level `=`), within code[p:e]; None for a declarator with no initializer (`let x;`, `x of list`)."""
    code = f.code
    angle = 0
    i = p
    while i < e:
        c = code[i]
        if c in "([{":
            i = f.close_of(i, e) + 1
            continue
        if c == "<":
            angle += 1
        elif c == ">" and code[i - 1] != "=":
            angle = max(0, angle - 1)
        elif c == "=" and angle == 0 and code[i - 1] not in "=!<>" and code[i + 1:i + 2] not in ("=", ">"):
            return f.ws(i + 1, e)
        i += 1
    return None


def _split(f: _File, a: int, b: int, angles: bool = False) -> list:
    """The top-level comma-separated segments of code[a:b], whitespace-trimmed: [(start, end)]. With
    `angles`, a comma inside <...> (a type argument list) does not split."""
    code = f.code
    b = min(b, len(code))                              # an unclosed group's end can sit one past the code
    out: list = []
    seg, i, angle = a, a, 0
    while i < b:
        c = code[i]
        if c in "([{":
            i = f.close_of(i, b) + 1
            continue
        if angles:
            if c == "<":
                angle += 1
            elif c == ">" and code[i - 1] != "=":
                angle = max(0, angle - 1)
        if c == "," and angle == 0:
            out.append((seg, i))
            seg = i + 1
        i += 1
    out.append((seg, min(b, len(code))))
    res = []
    for s, e in out:
        while s < e and code[s] in _WS_CHARS:
            s += 1
        while e > s and code[e - 1] in _WS_CHARS:
            e -= 1
        if s < e:
            res.append((s, e))
    return res


def _entry(f: _File, s: int, e: int) -> tuple:
    """One object-literal entry code[s:e] -> (key, value start, value end); a shorthand `{ handler }` is the
    identifier itself (its value is the binding of that name); value None for a method `handler() {}`; key
    None for a spread or anything unreadable."""
    code = f.code
    if code.startswith("...", s):
        return None, None, None
    key = None
    j = s
    km = _IDENT.match(code, s)
    if km:
        key, j = km.group(0), km.end()
        if key in ("async", "get", "set"):              # a modifier before a method's name: `async list() {}`
            k = f.ws(j, e)
            k = f.ws(k + 1, e) if code.startswith("*", k) else k
            mm = _IDENT.match(code, k, e)
            if mm and code[f.ws(mm.end(), e):f.ws(mm.end(), e) + 1] in ("(", "<"):
                return mm.group(0), None, None
    elif code[s] in "'\"":
        key = f.lit(s)
        q = code.find(code[s], s + 1, e)
        j = e if q < 0 else q + 1
    elif code[s] == "[":
        return None, None, None
    if key is None:
        return None, None, None
    j = f.ws(j, e)
    if j >= e:
        return (key, s, km.end()) if km else (key, None, None)     # shorthand: the name is the value
    if code[j] == ":":
        vs = f.ws(j + 1, e)
        return key, vs, e
    return key, None, None                              # method shorthand / something else


def _entries(f: _File, open_: int) -> dict:
    """The entries of the object literal whose `{` is at `open_`: {key: (value start, value end) | None (a
    method)}; a shorthand entry's value is its own identifier."""
    if f.code[open_:open_ + 1] != "{":
        return {}
    close = f.close_of(open_, len(f.code))
    out: dict = {}
    for s, e in _split(f, open_ + 1, close):
        key, vs, ve = _entry(f, s, e)
        if key is not None and key not in out:
            out[key] = (vs, ve) if vs is not None else None
    return out


def _object_members(f: _File, open_: int) -> dict:
    """What each key of the object literal whose `{` is at `open_` holds: ("id", name) for a shorthand or `key:
    name` entry, ("wrap", name, offset) for `key: catchAsync(name)` (a handler wrapped once, _wrapped_name), else
    ("def", offset of the key): a value or a method defined right there. A spread or a computed key is not read."""
    code = f.code
    out: dict = {}
    if code[open_:open_ + 1] != "{":
        return out
    for s, e in _split(f, open_ + 1, f.close_of(open_, len(code))):
        key, vs, ve = _entry(f, s, e)
        if key is None or key in out:
            continue
        val = code[vs:ve].strip() if vs is not None else ""
        w = _wrapped_name(f, vs, ve) if vs is not None and not _IDENT.fullmatch(val or "-") else None
        out[key] = ("id", val) if _IDENT.fullmatch(val or "-") else (("wrap", w, s) if w else ("def", s))
    return out


def _wrapped_name(f: _File, a: int, b: int):
    """The name a value wraps once, the route reader's rule for a final handler: `catchAsync(list)` -> "list" (a
    callee named like a handler wrapper, its first argument a name); else None."""
    v = _value(f, a, b)
    if v[0] != "call" or len(v[1]) != 1 or not _WRAPPER_NAME.match(v[1][0]):
        return None
    args = _split(f, v[2] + 1, v[3])
    w = _value(f, *args[0]) if args else ("other",)
    return w[1] if w[0] == "id" else None


def _pattern_names(f: _File, open_: int) -> list:
    """The names a destructuring pattern (`{ a, b: c, d = 1, ...e }` / `[a, , b]`) at `open_` binds; a nested
    pattern is not read."""
    code = f.code
    out = []
    for s, e in _split(f, open_ + 1, f.close_of(open_, len(code))):
        seg = code[s:e].strip()
        seg = seg[3:].strip() if seg.startswith("...") else seg
        seg = seg.split("=", 1)[0].strip()               # the default first: it may hold a `:` (a type, a ternary)
        if code[open_] == "{" and ":" in seg:
            seg = seg.split(":", 1)[1].strip()
        if _IDENT.fullmatch(seg):
            out.append(seg)
    return out


def _value_shape(f: _File, p: int):
    """What the expression at `p` makes, for a member read on it: ("obj", _object_members) for an object literal,
    ("cls", its members at their names, its `extends` or None) for a class expression (`class [X] [extends Y] { ...
    }`, whose methods the engine names bare), ("new", "X") for `new X(...)`, ("anon",) for a function (no member is
    a definition), else None."""
    code = f.code
    p = f.ws(p, len(code))
    if code.startswith("{", p):
        return ("obj", _object_members(f, p))
    nm = _NEW_RE.match(code, p)
    if nm:
        return ("new", re.sub(r"\s+", "", nm.group(1)))
    cm = _CLASS_KW.match(code, p)                        # a named class expression: its header starts after the name
    if cm is None or cm.group(1) in ("extends", "implements"):
        cm = _CLASS_EXPR.match(code, p)
    if cm:
        cls = _class_at(f, cm)
        out: dict = {}
        for mem in (_members(f, cls) if cls is not None else ()):
            if mem.name:
                out.setdefault(mem.name, ("def", mem.pos))
        return ("cls", out, cls.extends[0][1] if cls is not None and cls.extends else None)
    if _FN_HEAD.match(code, p) or _is_arrow(f, p, len(code)):
        return ("anon",)
    return None


_MEMBER_SET = re.compile(r"\.\s*([A-Za-z_$][\w$]*)\s*=(?![=>])\s*")


def _var_shapes(f: _File, names) -> dict:
    """The _value_shape of each top-level const / let / var of `names` (when it has one); an object's (or class
    expression's) members include the ones assigned by name later (`controller.list = ...`), found in one pass over
    the file."""
    out: dict = {}
    code = f.code
    names = set(names)
    for m in _VAR_DECL.finditer(code):                   # the top-level declarator (a nested one may come first)
        name = m.group(1)
        if name in names and name not in out and f.depth(m.start()) == 0:
            sh = _value_shape(f, m.end())
            out[name] = (sh[0], dict(sh[1])) + sh[2:] if sh is not None and sh[0] in ("obj", "cls") else sh
    out = {n: sh for n, sh in out.items() if sh is not None}
    objs = {n for n, sh in out.items() if sh[0] in ("obj", "cls")}
    for m in (_MEMBER_SET.finditer(code) if objs else ()):
        k = m.start() - 1
        while k >= 0 and code[k] in _WS_CHARS:
            k -= 1
        wm = _WORD_END.search(code, max(0, k - 63), k + 1) if k >= 0 else None
        if wm is None or wm.group(0) not in objs or (wm.start() and code[wm.start() - 1] == "."):
            continue
        members, key = out[wm.group(0)][1], m.group(1)
        if key in members:
            continue
        im = re.match(r"([A-Za-z_$][\w$]*)\s*(?:;|$|\n)", code[m.end():m.end() + 200])
        w = None if im else _wrapped_name(f, m.end(), _expr_end(f, m.end(), len(code)))
        members[key] = ("id", im.group(1)) if im else (("wrap", w, wm.start()) if w else ("def", wm.start()))
    return out


def _value(f: _File, a: int, b: int, _depth: int = 0) -> tuple:
    """Classify the expression code[a:b]: ('str', v) | ('dyn',) | ('id', name) | ('member', [parts]) |
    ('arr', a, b) | ('obj', a) | ('fn',) | ('call', [callee parts], open, close) | ('new', [parts]) |
    ('other',). A template whose `${ }` parts all name literal strings folds to ('str', v)."""
    code = f.code
    b = min(b, len(code))                              # an unclosed group's end can sit one past the code
    a = f.ws(a, b)
    while b > a and code[b - 1] in _WS_CHARS:
        b -= 1
    if a >= b:
        return ("other",)
    c = code[a]
    if c in "'\"`":
        close = code.find(c, a + 1, b + 1)
        if close == b - 1:
            v = f.lit(a)
            if v is None and c == "`":
                v = _fold_template(f, a, close, _depth)
            return ("str", v) if v is not None else ("dyn",)
        return ("other",)
    if c == "[":
        return ("arr", a, f.close_of(a, b)) if f.close_of(a, b) == b - 1 else ("other",)
    if c == "{":
        return ("obj", a) if f.close_of(a, b) == b - 1 else ("other",)
    if _FN_HEAD.match(code, a) or _is_arrow(f, a, b):
        return ("fn",)
    nm = _NEW_RE.match(code, a, b)
    if nm:
        parts = re.sub(r"\s+", "", nm.group(1)).split(".")
        j = nm.end()
        if j >= b:
            return ("new", parts)
        if code[j] == "(":
            close = f.close_of(j, b)
            if close >= b - 1:
                return ("new", parts)
            return ("newchain", parts, j, close, f.ws(close + 1, b))
        return ("other",)
    dm = _DOTTED.match(code, a, b)
    if dm:
        parts = re.sub(r"\s+", "", dm.group(0)).split(".")
        j = f.ws(dm.end(), b)
        if j >= b:
            return ("id", parts[0]) if len(parts) == 1 else ("member", parts)
        if code[j] == "<":                              # a type-argument list before a call
            gm = _TYPE_ARGS_CALL.match(code, j, b)
            if gm:
                j = f.ws(gm.end() - 1, b)
        if j < b and code[j] == "(":
            close = f.close_of(j, b)
            if close >= b - 1:
                return ("call", parts, j, close)
            return ("chain", parts, j, close, f.ws(close + 1, b))
    return ("other",)


_TPL_REF = re.compile(r"\s*(this\s*\.\s*)?([A-Za-z_$][\w$]*)\s*\}")


def _fold_template(f: _File, a: int, close: int, depth: int):
    """The value of the template literal src[a:close + 1] when every `${ }` in it is `this.<field>` (a field
    of the enclosing class) or a `const` declared once in this file, holding a literal string
    (`${this.path}/:id`, `${API}/login`), else None: a call, a member chain, an import, anything computed.
    Bounded: two levels of nesting, four references per template."""
    if depth > 2:
        return None
    src = f.src
    out, i = [], a + 1
    for _ in range(5):
        j = src.find("${", i, close)
        if j < 0:
            out.append(_unescape(src[i:close]))
            return "".join(out)
        m = _TPL_REF.match(src, j + 2, close)
        if not m or src[j - 1] == "\\":
            return None
        out.append(_unescape(src[i:j]))
        s = _ref_str(f, ("member", ["this", m.group(2)]) if m.group(1) else ("id", m.group(2)), a, depth)
        if s is None:
            return None
        out.append(s)
        i = m.end()
    return None


def _ref_str(f: _File, v: tuple, pos: int, depth: int = 0):
    """The literal string a reference read at `pos` holds, else None: an identifier naming a `const` in
    scope there (_const_span), or `this.<field>` naming a (non-static) field of the class around `pos`,
    initialized with a literal and never assigned by the class's code."""
    span = None
    if v[0] == "id":
        span = _const_span(f, v[1], pos)
    elif v[0] == "member" and len(v[1]) == 2 and v[1][0] == "this":
        cls = _enclosing_class(f, pos)
        mem = next((x for x in cls.members if x.kind == "prop" and x.name == v[1][1]
                    and "static" not in x.mods), None) if cls else None
        span = mem.init_span if mem and v[1][1] not in _written_fields(f, cls) else None
    w = _value(f, *span, _depth=depth + 1) if span else ("other",)
    return w[1] if w[0] == "str" else None


def _const_span(f: _File, name: str, pos: int):
    """The initializer span of the `const` `name` as read at `pos`, else None. Only a const whose declarator is
    the one binding of that name in the file (no other const / let / var declarator — initialized or not, a
    later one of a list —, for-of / for-in variable, parameter, catch clause, destructured name, function or
    class of that name anywhere in the file) and whose block holds `pos` (module level holds every position):
    its value at `pos` is then knowable from here."""
    got = f.const_inits.get(name)
    if got is None or f.declared.get(name, 0) != 1 or name in f.funcs or name in f.classes \
            or name in f.bound_names():
        return None
    s, e, at = got
    blk = f.block_of(at)
    if blk is not None and not (blk[0] < pos < blk[1]):
        return None
    return s, e


def _written_fields(f: _File, cls: _Class) -> set:
    """The fields the class body assigns (`this.path = path`, `this.base += '/v2'`): their initializer is
    not their value everywhere. One scan per class."""
    got = f.written_fields.get(cls.body)
    if got is None:
        got = {m.group(1) for m in _THIS_WRITE.finditer(f.code, cls.body, cls.end)}
        f.written_fields[cls.body] = got
    return got


def _lit_str(f: _File, a: int, b: int):
    """The literal string the expression code[a:b] is (a string, a template that folds) or a reference holds
    (_ref_str), else None."""
    v = _value(f, a, b)
    if v[0] == "str":
        return v[1]
    if v[0] in ("id", "member"):
        return _ref_str(f, v, a)
    return None


def _src_text(f: _File, a: int, b: int, cap: int = 120) -> str:
    """Short SOURCE text of a span (for an attr that names an unresolved / object argument)."""
    return " ".join(f.src[a:b].split())[:cap]


# ---- classes and members --------------------------------------------------------------------------
def _decorator_at(f: _File, i: int, end: int) -> tuple:
    """A decorator at code[i] == '@' -> ((last name segment, open, close), next index); (None, i + 1)
    when what follows `@` is not a decorator."""
    code = f.code
    dm = _DOTTED.match(code, i + 1)
    if not dm:
        return None, i + 1
    name = re.sub(r"\s+", "", dm.group(0)).rsplit(".", 1)[-1]
    j = f.ws(dm.end(), end)
    if j < end and code[j] == "(":
        close = f.close_of(j, end)
        return (name, j, close), min(close + 1, end)
    return (name, None, None), dm.end()


def _decorators_before(f: _File, k: int) -> tuple:
    """(decorators in source order, is_expression) for the class keyword at `k`: decorators and the
    export / default / abstract / declare modifiers are skipped backwards; what precedes them says whether
    the class is an expression (`= class`, `(class`, `return class`)."""
    code = f.code
    decos: list = []
    p = k
    while True:
        q = p - 1
        while q >= 0 and code[q] in _WS_CHARS:
            q -= 1
        if q < 0:
            return decos[::-1], False
        ch = code[q]
        if ch == ")":
            o = f.match.get(q)
            if o is None:
                return decos[::-1], False
            r = o - 1
            while r >= 0 and code[r] in _WS_CHARS:
                r -= 1
            t = r
            while t >= 0 and (_ID_CHARS.match(code[t]) or code[t] == "."):
                t -= 1
            name = code[t + 1:r + 1]
            if t >= 0 and code[t] == "@" and name:
                decos.append((name.rsplit(".", 1)[-1], o, q))
                p = t
                continue
            return decos[::-1], True
        if _ID_CHARS.match(ch):
            r = q
            while r >= 0 and (_ID_CHARS.match(code[r]) or code[r] == "."):
                r -= 1
            word = code[r + 1:q + 1]
            if word in ("export", "default", "abstract", "declare"):
                p = r + 1
                continue
            if r >= 0 and code[r] == "@":
                decos.append((word.rsplit(".", 1)[-1], None, None))
                p = r
                continue
            return decos[::-1], word in ("return", "yield", "await", "new", "typeof", "void", "in", "of")
        return decos[::-1], ch in "=(,:?[!&|+-*%<>~^"


def _class_at(f: _File, m, iface: bool = False):
    """The class / interface declared by the keyword match `m`, or None."""
    code = f.code
    n = len(code)
    name = m.group(1)
    i = m.end()
    lim = min(n, i + _MAX_HEADER)
    angle = 0
    body = -1
    while i < lim:
        c = code[i]
        if c == "<":
            angle += 1
        elif c == ">" and code[i - 1] != "=":
            angle = max(0, angle - 1)
        elif c in "([":
            i = f.close_of(i, lim) + 1
            continue
        elif c == "{":
            if angle == 0:
                body = i
                break
            i = f.close_of(i, lim) + 1
            continue
        elif c == ";" and angle == 0:
            break
        i += 1
    if body < 0:
        return None
    decos, is_expr = ([], False) if iface else _decorators_before(f, m.start())
    end = f.match.get(body, n)
    cls = _Class(name, m.start(), body, end, decos, is_expr, f.depth(m.start()) == 0, iface=iface)
    header = code[m.end():body]
    _parse_heritage(f, cls, header)
    return cls


def _heritage_names(text: str) -> list:
    """`A, ns.B<T>, C` -> [(bare, raw)] — a call (mixin) or anything non-nominal is dropped."""
    out = []
    depth, cur, parts = 0, [], []
    for ch in text:
        if ch in "<([{":
            depth += 1
        elif ch in ">)]}":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    for p in parts:
        p = p.strip()
        m = re.match(r"(" + _DOTTED.pattern + r")\s*(<.*>)?\s*$", p, re.S)
        if not m:
            continue
        raw = re.sub(r"\s+", "", m.group(1))
        out.append((raw.rsplit(".", 1)[-1], raw))
    return out


def _strip_type_params(header: str) -> str:
    """Drop a leading `<T extends X = Y>` type-parameter list (its `extends` is a constraint)."""
    h = header.lstrip()
    if not h.startswith("<"):
        return header
    depth = 0
    for i, ch in enumerate(h):
        if ch == "<":
            depth += 1
        elif ch == ">" and (i == 0 or h[i - 1] != "="):
            depth -= 1
            if depth == 0:
                return h[i + 1:]
    return ""


def _parse_heritage(f: _File, cls: _Class, header: str) -> None:
    header = _strip_type_params(header)
    em = re.search(r"\bextends\b([\s\S]*?)(?=\bimplements\b|$)", header)
    if em:
        cls.extends = _heritage_names(em.group(1))
        if not cls.iface:
            cls.extends = cls.extends[:1]
    im = re.search(r"\bimplements\b([\s\S]*)$", header)
    if im and not cls.iface:
        cls.implements = _heritage_names(im.group(1))


_SELF_BIND = re.compile(r"this\s*\.\s*([A-Za-z_$][\w$]*)\s*\.\s*bind\s*\(")
# `X.x = ...` / `X.prototype.x = ...`: a member a statement sets on a class (or a constructor function) by name
_STATIC_WRITE = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\.\s*(?:prototype\s*\.\s*)?([A-Za-z_$][\w$]*)"
                           r"\s*=(?![=>])")


def _static_writes(f: _File) -> dict:
    """name -> the members module-level statements set on it (`X.x = ...`, `X.prototype.x = ...`): one pass over
    the file, whatever number of classes asks."""
    out: dict = {}
    for m in _STATIC_WRITE.finditer(f.code):
        if f.depth(m.start()) == 0:
            out.setdefault(m.group(1), set()).add(m.group(2))
    return out


def _own_names(f: _File, cls: _Class, static: dict) -> set:
    """The members a class holds itself: its body's, each `this.x = ...` its code writes (not `this.x =
    this.x.bind(this)`, which keeps the inherited x), and each `X.x = ...` / `X.prototype.x = ...` at top level
    (`static`: _static_writes of the file)."""
    names = {m.name for m in _members(f, cls) if m.name}
    code = f.code
    for m in _THIS_WRITE.finditer(code, cls.body, cls.end):
        bm = _SELF_BIND.match(code, f.ws(m.end(), cls.end))
        if bm is None or bm.group(1) != m.group(1):
            names.add(m.group(1))
    if cls.name:
        names |= static.get(cls.name, set())
    return names


def _members(f: _File, cls: _Class) -> list:
    """The members of a class body: methods (with their parameter lists) and properties (with their
    type annotation / initializer spans), each with its decorators. Jumps over every method body and
    initializer, so a class costs O(its own length)."""
    code = f.code
    b0, b1 = cls.body + 1, cls.end
    out: list = []
    p = b0
    while p < b1:
        p = f.ws(p, b1)
        while p < b1 and code[p] in ";,":
            p = f.ws(p + 1, b1)
        if p >= b1:
            break
        start = p
        decos: list = []
        while p < b1 and code[p] == "@":
            d, p = _decorator_at(f, p, b1)
            if d is None:
                break
            decos.append(d)
            p = f.ws(p, b1)
        mods: list = []
        while p < b1:
            wm = _IDENT.match(code, p)
            if not wm or wm.group(0) not in _MEMBER_MODIFIERS:
                break
            q = f.ws(wm.end(), b1)
            if q == wm.end() or q >= b1 or code[q] in "(:=?;!<,}":
                break                                   # the word is the member's own name
            mods.append(wm.group(0))
            p = q
        if p < b1 and code[p] == "{":                   # a static initialization block
            p = f.close_of(p, b1) + 1
            continue
        if p < b1 and code[p] == "*":
            p = f.ws(p + 1, b1)
        name, name_pos = None, p
        if p < b1:
            c = code[p]
            if c == "#":
                wm = _IDENT.match(code, p + 1)
                if wm:
                    name, p = "#" + wm.group(0), wm.end()
                else:
                    p += 1
            elif c in "'\"":
                q = code.find(c, p + 1, b1)
                p = b1 if q < 0 else q + 1
            elif c == "[":
                p = f.close_of(p, b1) + 1
            else:
                wm = _IDENT.match(code, p) or _NUMBER_NAME.match(code, p)
                if wm:
                    name = wm.group(0) if not wm.group(0)[0].isdigit() else None
                    p = wm.end()
                else:
                    p = start + 1                       # not a member: skip one char
                    continue
        p = f.ws(p, b1)
        if p < b1 and code[p] in "?!":
            p = f.ws(p + 1, b1)
        if p < b1 and code[p] == "<":
            gm = _GENERIC_METHOD.match(code, p, min(b1, p + 400))
            if gm:
                p = f.ws(gm.end() - 1, b1)
        if p < b1 and code[p] == "(":
            pc = f.close_of(p, b1)
            end = _method_end(f, pc + 1, b1)
            out.append(_Member("method", name, name_pos, decos, mods, (p, pc), None, None, end))
        else:
            end, tspan, ispan = _property_end(f, p, b1)
            out.append(_Member("prop", name, name_pos, decos, mods, None, tspan, ispan, end))
        p = max(end, start + 1)
    return out


def _method_end(f: _File, q: int, b1: int) -> int:
    """After a method's `)`: skip a return type annotation, then its body `{...}` (or a `;`)."""
    code = f.code
    q = f.ws(q, b1)
    if q >= b1:
        return b1
    if code[q] == "{":
        return f.close_of(q, b1) + 1
    if code[q] == ";":
        return q + 1
    if code[q] != ":":
        return q
    angle = 0
    prev, pprev = ":", ""
    i = q + 1
    while i < b1:
        c = code[i]
        if c in _WS_CHARS:
            i += 1
            continue
        if c == "<":
            angle += 1
        elif c == ">":
            if prev != "=":
                angle = max(0, angle - 1)
        elif c in "([":
            i = f.close_of(i, b1) + 1
            prev, pprev = ")", prev
            continue
        elif c == "{":
            arrow = prev == ">" and pprev == "="          # `(): () => { a: T } {`: a type literal
            if angle > 0 or prev in ":|&,<(" or arrow:
                i = f.close_of(i, b1) + 1
                prev, pprev = "}", prev
                continue
            return f.close_of(i, b1) + 1
        elif c == ";" and angle == 0:
            return i + 1
        prev, pprev = c, prev
        i += 1
    return b1


_CONT_PREV = set("=,(:[{|&?.+-*/%<!~^")
_CONT_NEXT = set(".?:|&=+-*/%<>)]`(")


def _property_end(f: _File, p: int, b1: int) -> tuple:
    """(end, type span, initializer span) of a property declaration starting after its name."""
    code = f.code
    tspan = ispan = None
    i = p
    seg_start = None
    mode = None
    if i < b1 and code[i] == ":":
        mode, seg_start, i = "type", i + 1, i + 1
    elif i < b1 and code[i] == "=" and not code.startswith("=>", i):
        mode, seg_start, i = "init", i + 1, i + 1
    prev, pprev = "", ""
    while i < b1:
        c = code[i]
        if c in "([{":
            i = f.close_of(i, b1) + 1
            prev, pprev = ")", prev
            continue
        if c == ";":
            break
        if c == "=" and mode == "type" and not code.startswith("=>", i) and not code.startswith("==", i) \
                and (i == 0 or code[i - 1] not in "=!<>"):
            tspan = (seg_start, i)
            mode, seg_start = "init", i + 1
            i += 1
            prev, pprev = "=", prev
            continue
        if c == "\n":
            k = f.ws(i, b1)
            arrow = prev == ">" and pprev == "="
            if (prev not in _CONT_PREV and not arrow) and (k >= b1 or code[k] not in _CONT_NEXT):
                break
        elif c not in _WS_CHARS:
            prev, pprev = c, prev
        i += 1
    if mode == "type" and seg_start is not None:
        tspan = (seg_start, i)
    elif mode == "init" and seg_start is not None:
        ispan = (seg_start, i)
    return i, tspan, ispan


def _params(f: _File, open_: int, close: int) -> list:
    """A parameter list -> [(name | None, decorators, type text | None, access-modified?)]."""
    code = f.code
    out = []
    for s, e in _split(f, open_ + 1, close, angles=True):
        p = s
        decos = []
        while p < e and code[p] == "@":
            d, p = _decorator_at(f, p, e)
            if d is None:
                break
            decos.append(d)
            p = f.ws(p, e)
        access = False
        while p < e:
            wm = _IDENT.match(code, p)
            if not wm or wm.group(0) not in _ACCESS_WORDS:
                break
            q = f.ws(wm.end(), e)
            if q == wm.end() or q >= e or code[q] in ":?,=)":
                break
            access, p = True, q
        if code.startswith("...", p):
            continue
        nm = _IDENT.match(code, p)
        name = nm.group(0) if nm else None
        p = nm.end() if nm else (f.close_of(p, e) + 1 if p < e and code[p] in "{[" else p)
        p = f.ws(p, e)
        if p < e and code[p] == "?":
            p = f.ws(p + 1, e)
        typ = None
        if p < e and code[p] == ":":
            t0, t = p + 1, p + 1
            while t < e:
                c = code[t]
                if c in "([{":
                    t = f.close_of(t, e) + 1
                    continue
                if c == "=" and not code.startswith("=>", t) and code[t - 1] not in "=!<>":
                    break
                t += 1
            typ = code[t0:t].strip()
        out.append((name, decos, typ, access))
    return out


# ---- type references --------------------------------------------------------------------------------
def _type_ref(text) -> tuple:
    """A type annotation -> (bare class name, root identifier) of the named type it denotes, or ("", "")
    for a union, literal, function, array or anything non-nominal. `ns.Foo<T>` -> ("Foo", "ns")."""
    t = (text or "").strip()
    if not t or any(ch in t for ch in "|&") or t[0] in "{([`'\"" or t.endswith("]"):
        return "", ""
    m = re.match(r"(" + _DOTTED.pattern + r")\s*(<[\s\S]*>)?\s*$", t)
    if not m:
        return "", ""
    raw = re.sub(r"\s+", "", m.group(1))
    return raw.rsplit(".", 1)[-1], raw.split(".", 1)[0]


def _element_type(text) -> tuple:
    """A relation property's type -> its entity: `Photo[]`, `Array<Photo>`, `Promise<Photo[]>`,
    `Relation<User>`, `Collection<Book>`, `Ref<Author>`, `User | null` -> (bare, root) or ("", "")."""
    t = (text or "").strip()
    t = re.sub(r"\s*\|\s*(?:null|undefined)\b", "", t).strip()
    for _ in range(4):
        m = re.match(r"([A-Za-z_$][\w$]*)\s*<([\s\S]*)>\s*$", t)
        if m and m.group(1) in _REL_WRAPPERS:
            t = m.group(2).strip()
            continue
        break
    while t.endswith("[]"):
        t = t[:-2].strip()
    return _type_ref(t)


def _class_target(f: _File, bare: str, root: str) -> tuple:
    """(name, conf hint, scope) for a TYPE reference, or ("", "", "") when it names nothing of this repo: a
    lowercase name, a type imported from an external package, or a scalar / runtime / DOM global (`Event`,
    `Error`, `Map`) the file neither declares nor imports. An imported type resolves under its EXPORTED name
    (`import { Foo as Bar }` -> Foo), preferring the module its import names (a "pref:" scope: edge_resolve
    falls back to the global name rule when that module defines none; finalize places it exactly); one
    imported from the repo is a repo class whatever its name (`import { Event } from './event.entity'`)."""
    if not bare or not bare[:1].isupper():
        return "", "", ""
    imp = f.imports.get(root)
    if imp is None:
        if root != bare:
            return "", "", ""                           # a global namespace (Express.Request, NodeJS.X)
        if bare in f.classes:
            return bare, "LOCAL", ""
        return ("", "", "") if bare in _TS_NON_ENTITY else (bare, "NAME", "")
    spec, imported = imp
    if is_package(spec):
        return "", "", ""
    name = imported if (root == bare and imported not in ("default", "*", "=")) else bare
    if not name[:1].isupper():
        return "", "", ""
    if root == bare:
        note = "default" if imported in ("default", "=") else ""
    else:                                               # ns.Base: a member of what `ns` binds
        note = "" if imported == "*" else imported
    return name, "NAME", _pref_scope(f, spec, note)


def _router_type(f: _File, text):
    """The framework of a type annotation naming an app / router type its framework module exports
    (`Router` / `Express` imported from express, `express.Router` on the module's import, `FastifyInstance`,
    `Hono`, a koa-router default import; `| undefined` allowed), else None. An array is no router."""
    bare, root = _type_ref(_strip_nullish(text))
    imp = f.imports.get(root) if bare else None
    fw = _FRAMEWORK_SPECS.get(imp[0]) if imp else None
    if fw is None:
        return None
    spec, imported = imp
    if root == bare:
        if imported in _ROUTER_TYPE_NAMES or (imported in ("default", "=") and spec in _ROUTER_CLASS_SPECS):
            return fw
        return None
    return fw if imported in ("default", "*", "=") and bare in _ROUTER_TYPE_NAMES else None


def _field_class(f: _File, cls: _Class, field: str) -> tuple:
    """The class of `this.<field>` inside `cls` -> (name, conf hint, scope), ("", "", "") when unknown: the
    field's `new X()` initializer or type annotation, a constructor parameter property's type, or a
    `this.<field> = new X()` assignment in the class body."""
    key = (cls.body, field)
    got = f.field_classes.get(key)
    if got is not None:
        return got
    got = "", "", ""
    for mem in cls.members:
        if mem.kind == "prop" and mem.name == field:
            v = _value(f, *mem.init_span) if mem.init_span else ("other",)
            if v[0] == "new":
                got = _class_target(f, v[1][-1], v[1][0])
            if not got[0] and mem.type_span:
                got = _class_target(f, *_type_ref(_strip_nullish(f.code[mem.type_span[0]:mem.type_span[1]])))
        elif mem.kind == "method" and mem.name == "constructor" and mem.params:
            for pname, _d, ptype, access in _params(f, *mem.params):
                if access and pname == field and ptype:
                    got = _class_target(f, *_type_ref(_strip_nullish(ptype)))
        if got[0]:
            break
    if not got[0]:
        for m in _THIS_ASSIGN.finditer(f.code, cls.body, cls.end):
            if m.group(1) == field:
                v = _value(f, m.end(), _expr_end(f, m.end(), cls.end))
                if v[0] == "new":
                    got = _class_target(f, v[1][-1], v[1][0])
                    if got[0]:
                        break
    f.field_classes[key] = got
    return got


def _strip_nullish(text: str) -> str:
    return re.sub(r"\s*\|\s*(?:null|undefined)\b", "", (text or "").strip()).strip()


def _arg_class(f: _File, a: int, b: int) -> tuple:
    """A decorator argument naming a class -> (bare, root): `X`, `ns.X`, `forwardRef(() => X)`,
    `() => X`, `type => X`, `new X()`, `X.name` (Mongoose InjectModel)."""
    v = _value(f, a, b)
    if v[0] == "id":
        return v[1], v[1]
    if v[0] == "member":
        parts = v[1]
        if parts[-1] == "name" and len(parts) == 2:
            return parts[0], parts[0]
        return parts[-1], parts[0]
    if v[0] == "new":
        return v[1][-1], v[1][0]
    if v[0] == "call" and v[1][-1] == "forwardRef":
        args = _split(f, v[2] + 1, v[3])
        return _arg_class(f, *args[0]) if args else ("", "")
    if v[0] == "fn":
        return _arrow_body_class(f, a, b)
    return "", ""


def _arrow_body_class(f: _File, a: int, b: int) -> tuple:
    """`() => X` / `type => X` / `(type) => X` / `() => [X]`'s X as (bare, root)."""
    code = f.code
    k = code.find("=>", a, b)
    if k < 0:
        return "", ""
    body = code[k + 2:b].strip()
    if body.startswith("[") and body.endswith("]"):
        body = body[1:-1].strip()
    m = re.match(r"(" + _DOTTED.pattern + r")\s*$", body)
    if not m:
        return "", ""
    raw = re.sub(r"\s+", "", m.group(1))
    return raw.rsplit(".", 1)[-1], raw.split(".", 1)[0]


# ---- path helpers -----------------------------------------------------------------------------------
def _join_path(*parts) -> str:
    """URL-join route path parts the way Nest / Express-style routers do: one '/' between parts, a
    leading '/', no trailing '/' (except the root)."""
    segs = []
    for p in parts:
        if p is None:
            continue
        for s in str(p).split("/"):
            if s:
                segs.append(s)
    return "/" + "/".join(segs)


def _concat_path(prefix, path) -> str:
    """An Express-style mount: prefix + path, joined with exactly one '/', the root path collapsing."""
    if not prefix:
        return path if path else "/"
    pre = "/" + prefix.strip("/") if prefix.strip("/") else ""
    if not path or path == "/":
        return pre or "/"
    return pre + "/" + path.lstrip("/")


def _str_list(f: _File, a: int, b: int):
    """A string literal or an array of string literals -> [values]; None when anything is not literal. A
    variable of this file or a `this.<field>` holding a literal string counts as that literal."""
    v = _value(f, a, b)
    if v[0] == "str":
        return [v[1]]
    if v[0] in ("id", "member"):
        s = _ref_str(f, v, a)
        return [s] if s is not None else None
    if v[0] == "arr":
        out = []
        for s, e in _split(f, v[1] + 1, v[2]):
            w = _value(f, s, e)
            if w[0] in ("id", "member"):
                r = _ref_str(f, w, s)
                w = ("str", r) if r is not None else w
            if w[0] != "str":
                return None
            out.append(w[1])
        return out
    return None


# ---- the per-file recognizer ----------------------------------------------------------------------
class _Emitter:
    def __init__(self, f: _File):
        self.f = f
        self.edges: list = []

    def emit(self, src, dst, et, conf, attrs, scope=""):
        if src and dst and conf:
            self.edges.append(TypedEdge(src, self.f.rel, dst, et, conf, attrs, scope))


def _skip_file(source: str, rel: str) -> bool:
    """A declaration file, a minified / bundled file by name, compiled JavaScript (a transpiler's or bundler's
    runtime marker in its head, a source-map trailer: the same classes as the source it was built from, which
    graphify skips when its directory is ignored), or a file that LOOKS minified: a 2000+ char line in a file
    whose lines average 200+ chars (one long string in a hand-written file does not count)."""
    low = rel.lower()
    if low.endswith(_DECLARATION_SUFFIXES) or low.endswith(_MINIFIED_SUFFIXES):
        return True
    if low.endswith(_COMPILED_JS_EXTS):
        head = source[:_COMPILED_HEAD]
        if any(m in head for m in _COMPILED_JS_MARKERS) or "//# sourceMappingURL=" in source[-512:]:
            return True
    n = len(source)
    if n < _MINIFIED_LINE:
        return False
    lines = source.count("\n") + 1
    if n / lines < _MINIFIED_AVG:
        return False
    i = 0
    while i < n:
        j = source.find("\n", i)
        j = n if j < 0 else j
        if j - i >= _MINIFIED_LINE:
            return True
        i = j + 1
    return False


def analyze(source: str, rel: str):
    """The lexed file with its module facts and classes (members scanned), or None for a skipped file."""
    if _skip_file(source, rel):
        return None
    f = _File(source, rel)
    for cls in f.classes.values():
        if not cls.iface:
            cls.members = _members(f, cls)
    return f


def recognize(source: str, rel: str) -> tuple:
    """PURE per-file core (the registry contract): TS/JS source -> (supplemental_nodes, typed_edges).
    Never raises on malformed source — anything unreadable is simply skipped."""
    f = analyze(source, rel)
    if f is None:
        return [], []
    em = _Emitter(f)
    for cls in f.classes.values():
        _class_edges(f, cls, em)
    router = _RouterFacts(f)
    router.emit_routes(em, _World.single(f, router))
    _next_routes(f, em)
    _angular_routes(f, em)
    nodes = []
    for cls in f.classes.values():
        if not cls.top or cls.is_expr:
            continue
        nodes.append({
            "key": f"{rel}::{cls.name}:{f.line(cls.kw)}",
            "label": "Class",
            "name": cls.name,
            "source_file": rel,
            "start_line": f.line(cls.kw),
            "documented": False,
            "body_summary": _summary(f, cls),
        })
        # A handler held by a property (`list = async (req, res) => {}`, `list = asyncHandler(...)`) is a
        # route target: graphify 0.8.39 node-ifies neither, later engines only the plain function (and as
        # `Class.prop` at the property's line, so theirs wins the merge).
        seen = set()
        for mem in cls.members:
            what = _fn_property(f, mem)
            name = f"{cls.name}.{mem.name}"
            if what is None or name in seen:
                continue
            seen.add(name)
            line = f.line(mem.pos)
            nodes.append({
                "key": f"{rel}::{name}:{line}",
                "label": "Method",
                "name": name,
                "source_file": rel,
                "start_line": line,
                "documented": False,
                "body_summary": f"{name}: {'TypeScript' if f.ts else 'JavaScript'} method ({what})"[:300],
            })
    return nodes, em.edges


def _fn_property(f: _File, mem: _Member):
    """What a class property holding a function is ("function-valued property" / "handler wrapped by X"
    for `list = asyncHandler(async (req, res) => ...)`), or None: any other member, a #private name. A
    property wrapped by a call is a handler only for a known handler wrapper (asyncHandler, catchAsync, ...)
    or a wrapped function with handler-shaped parameters (`(req, res)`, `(ctx)`, `(request, reply)`):
    `config = tryParse(() => ...)` or `total = asyncComputed(async () => ...)` holds a value."""
    if mem.kind != "prop" or not mem.name or mem.name.startswith("#") or not mem.init_span:
        return None
    v = _value(f, *mem.init_span)
    if v[0] == "fn":
        return "function-valued property"
    if v[0] == "call" and len(v[1]) == 1 and _WRAPPER_NAME.match(v[1][0]):
        args = _split(f, v[2] + 1, v[3])
        fns = [a for a in ((args[0], args[-1]) if args else ()) if _value(f, *a)[0] == "fn"]
        if fns and (v[1][0] in _HANDLER_WRAPPERS or any(_handler_shaped(f, *a) for a in fns)):
            return f"handler wrapped by {v[1][0]}"
    return None


def _handler_shaped(f: _File, a: int, b: int) -> bool:
    """The function literal code[a:b] takes a request handler's parameters: one of its first two is named
    req / request / res / response / reply / ctx / context / c / next."""
    code = f.code
    j = f.ws(a, b)
    am = _ASYNC_RE.match(code, j, b)
    if am and not code.startswith("=>", am.end()):
        j = am.end()
    fm = _FN_HEAD.match(code, j, b)
    if fm:
        j = code.find("(", fm.end(), b)
        if j < 0:
            return False
    if j < b and code[j] == "(":
        names = [p[0] for p in _params(f, j, f.close_of(j, b))[:2]]
    else:
        im = _IDENT.match(code, j, b)
        names = [im.group(0)] if im else []
    return any(n in _HANDLER_PARAMS for n in names if n)


# ---- classes: NestJS / TypeORM / Angular / heritage -----------------------------------------------
def _deco_map(decos) -> dict:
    out: dict = {}
    for name, o, c in decos:
        out.setdefault(name, (o, c))
    return out


def _deco_args(f: _File, span) -> list:
    o, c = span
    return _split(f, o + 1, c) if o is not None else []


def _framework_of(f: _File, deco: str, default: str) -> str:
    """The framework a decorator comes from, by its import; `default` when the import does not say."""
    spec = f.import_spec(deco) or ""
    if spec.startswith("@angular/"):
        return "angular"
    if spec.startswith("routing-controllers"):
        return "routing-controllers"
    if spec == "tsoa" or spec.startswith("tsoa/"):
        return "tsoa"
    if spec.startswith("@nestjs/"):
        return "nestjs"
    return default


def _kind_phrase(f: _File, cls: _Class, cd: dict) -> str:
    if cls.iface:
        return "TypeScript interface"
    for deco, phrase in (("Controller", "controller"), ("JsonController", "controller"),
                         ("Module", "module"), ("Resolver", "resolver"), ("WebSocketGateway", "gateway"),
                         ("Processor", "queue processor"), ("Catch", "exception filter"),
                         ("Injectable", "provider")):
        if deco in cd:
            fw = _framework_of(f, deco, "nestjs")
            return ("Angular service" if fw == "angular" and deco == "Injectable"
                    else f"{'routing-controllers' if fw == 'routing-controllers' else 'NestJS'} {phrase}")
    for deco in ("Component", "Directive", "Pipe", "NgModule"):
        if deco in cd:
            return "Angular " + ("module" if deco == "NgModule" else deco.lower())
    ent = next((d for d in _ENTITY_DECOS if d in cd), None)
    if ent:
        return ("MikroORM" if _orm_of(f, ent) == "mikro-orm" else "TypeORM") + " entity"
    return ("TypeScript" if f.ts else "JavaScript") + " class"


def _orm_of(f: _File, deco: str) -> str:
    """"mikro-orm" for a decorator imported from @mikro-orm/*, else "typeorm" (the `via` of an ORM edge)."""
    return "mikro-orm" if (f.import_spec(deco) or "").startswith("@mikro-orm/") else "typeorm"


def _summary(f: _File, cls: _Class) -> str:
    base = f"{cls.name}: {_kind_phrase(f, cls, _deco_map(cls.decos))}"
    if cls.facts:
        base += "; " + "; ".join(cls.facts[:8])
    return base[:300]


def _line_scope(f: _File, cls: _Class, tgt: str, conf: str, scope: str) -> str:
    """A heritage edge's scope with its ends' declaration lines in front ("L<class line>,<base line>|"; the
    base's 0 when it is declared in another file, which finalize fills in): the engine can node-ify one name
    twice in a file (zod's `interface X` and its `const X = ...` companion), and the lines pick the
    declarations (edge_resolve._at_line)."""
    base = f.classes.get(tgt) if conf == "LOCAL" else None
    return f"L{f.line(cls.kw)},{f.line(base.kw) if base is not None else 0}|{scope}"


def _class_edges(f: _File, cls: _Class, em: _Emitter) -> None:
    name = cls.name
    for bare, raw in cls.extends:
        root = raw.split(".", 1)[0]
        tgt, conf, scope = _class_target(f, bare, root)
        if tgt and (tgt != name or scope):              # an imported X is never this class X
            em.emit(name, tgt, "INHERITS", conf, {"raw_parent": raw} if "." in raw else {},
                    _line_scope(f, cls, tgt, conf, scope))
            cls.facts.append("inherits " + tgt)
    for bare, raw in cls.implements:
        root = raw.split(".", 1)[0]
        tgt, conf, scope = _class_target(f, bare, root)
        if tgt and (tgt != name or scope):
            em.emit(name, tgt, "IMPLEMENTS", "NAME", {"raw_parent": raw} if "." in raw else {},
                    _line_scope(f, cls, tgt, conf, scope))
            cls.facts.append("implements " + tgt)
    if cls.iface or cls.is_expr:
        return
    cd = _deco_map(cls.decos)
    di_class = any(d in cd for d in _DI_CLASS_DECOS)
    # @Controller(prefix) / @Controller({ path, version }) / routing-controllers @JsonController / tsoa @Route
    prefixes, prefix_unres, version = [""], "", None
    ctrl = next((d for d in ("Controller", "JsonController") if d in cd), None)
    if "Route" in cd and _framework_of(f, "Route", "") == "tsoa":
        ctrl = "Route"
    if ctrl:
        args = _deco_args(f, cd[ctrl])
        if args:
            v = _value(f, *args[0])
            if v[0] == "obj":
                ents = _entries(f, v[1])
                pv = ents.get("path")
                if pv:
                    lst = _str_list(f, *pv)
                    if lst is None:
                        prefix_unres = _src_text(f, *pv)
                    else:
                        prefixes = lst or [""]
                vv = ents.get("version")
                if vv:
                    w = _value(f, *vv)
                    version = w[1] if w[0] == "str" else _src_text(f, *vv)
            else:
                lst = _str_list(f, *args[0])
                if lst is None:
                    prefix_unres = _src_text(f, *args[0])
                else:
                    prefixes = lst or [""]
    else:
        prefix_unres = _NO_CONTROLLER                    # route decorators here serve under each subclass
    framework = _framework_of(f, ctrl, "nestjs") if ctrl else "nestjs"
    # class-level lifecycle hooks
    for deco, event in _HOOK_DECOS.items():
        if deco in cd:
            _hook_edges(f, em, name, cd[deco], event, cls)
    # @Module({ imports, controllers, providers })
    if "Module" in cd or "NgModule" in cd:
        _module_edges(f, em, cls, cd.get("Module") or cd.get("NgModule"))
    # Bull @Processor('queue')
    queue = None
    if "Processor" in cd:
        args = _deco_args(f, cd["Processor"])
        if args:
            v = _value(f, *args[0])
            queue = v[1] if v[0] == "str" else None
    # JS Nest: @Dependencies(A, B) declares the constructor's injected classes
    if "Dependencies" in cd:
        for s, e in _deco_args(f, cd["Dependencies"]):
            bare, root = _arg_class(f, s, e)
            tgt, _c, scope = _class_target(f, bare, root)
            if tgt and (tgt != name or scope):
                em.emit(name, tgt, "DEPENDS_ON", "NAME", {"via": "ctor"}, scope)
    _middleware_consumer(f, em, cls)
    ctor_done = False
    for mem in reversed(cls.members):                    # the implementation signature is the last one
        if mem.kind == "method" and mem.name == "constructor" and not ctor_done:
            ctor_done = True
            if di_class:
                _ctor_edges(f, em, cls, mem)
    for mem in cls.members:
        md = _deco_map(mem.decos)
        if mem.kind == "method":
            if not mem.name or mem.name == "constructor":
                continue
            handler = f"{name}.{mem.name}"
            for deco, verb in _HTTP_DECOS.items():
                if deco not in md or (deco in _NEST_ONLY_HTTP
                                      and not (f.import_spec(deco) or "").startswith("@nestjs/")):
                    continue
                args = _deco_args(f, md[deco])
                paths, unres = [""], ""
                if args:
                    lst = _str_list(f, *args[0])
                    if lst is None:
                        paths, unres = None, _src_text(f, *args[0])
                    else:
                        paths = lst or [""]
                attrs = {"method": verb}
                if paths is not None:
                    full = sorted({_join_path(pre, p) for pre in prefixes for p in paths})[:_MAX_PATHS]
                    attrs["path"] = full[0]
                    if len(full) > 1:
                        attrs["paths"] = full
                if prefix_unres:
                    attrs["prefix_unresolved"] = prefix_unres
                if unres:
                    attrs["path_unresolved"] = unres
                if version is not None:
                    attrs["version"] = version
                attrs["framework"] = _framework_of(f, deco, framework)
                em.emit(name, handler, "ROUTES_TO", "LOCAL", attrs)
                cls.facts.append(f"route {verb} {attrs.get('path', '')}".rstrip())
            for deco, trigger in _SCHED_DECOS.items():
                if deco in md:
                    attrs = {"task": handler, "trigger": trigger}
                    args = _deco_args(f, md[deco])
                    if args:
                        last = args[-1] if trigger != "cron" else args[0]
                        v = _value(f, *last)
                        attrs["schedule"] = v[1] if v[0] == "str" else _src_text(f, *last)
                    em.emit(name, handler, "SCHEDULED_AS", "LOCAL", attrs)
                    cls.facts.append(f"{trigger} job {mem.name}")
            if "Process" in md and "Processor" in cd:
                attrs = {"task": handler}
                if queue:
                    attrs["queue"] = queue
                args = _deco_args(f, md["Process"])
                if args:
                    v = _value(f, *args[0])
                    if v[0] == "str":
                        attrs["job"] = v[1]
                em.emit(name, handler, "SCHEDULED_AS", "LOCAL", attrs)
                cls.facts.append(f"queue job {mem.name}")
            for deco, transport in _EVENT_DECOS.items():
                if deco not in md:
                    continue
                args = _deco_args(f, md[deco])
                attrs = {}
                if args:
                    v = _value(f, *args[0])
                    if v[0] == "str":
                        attrs["event"] = v[1]
                    else:
                        attrs["pattern"] = _src_text(f, *args[0])
                if transport:
                    attrs["transport"] = transport
                em.emit(name, handler, "TRIGGERS", "LOCAL", attrs)
                cls.facts.append(f"on {attrs.get('event') or attrs.get('pattern', deco)}")
            for deco, event in _HOOK_DECOS.items():
                if deco in md:
                    _hook_edges(f, em, handler, md[deco], event, cls)
            for deco in md:
                if deco in _LISTENER_DECOS:              # @BeforeInsert() normalize(): runs on that event
                    em.emit(name, handler, "HOOKS", "LOCAL", {"event": deco})
                    cls.facts.append(f"{deco} {mem.name}")
        else:
            if not mem.name:
                continue
            rel_deco = next((d for d in _REL_DECOS if d in md), None)
            if rel_deco:
                _relation_edges(f, em, cls, mem, md, rel_deco)
                continue
            if di_class:
                _property_di(f, em, cls, mem, md)


_CONSUMER_CHAIN = re.compile(r"\s*\.\s*(apply|exclude|forRoutes)\s*\(")


def _middleware_consumer(f: _File, em: _Emitter, cls: _Class) -> None:
    """NestJS module middleware: `configure(consumer) { consumer.apply(A, B).exclude(...).forRoutes(...) }`
    -> HOOKS(module class -> A / B, event "middleware"), the routes it covers in attrs (paths, controllers)."""
    code = f.code
    for mem in cls.members:
        if mem.kind != "method" or mem.name != "configure" or not mem.params:
            continue
        params = _params(f, *mem.params)
        recv = params[0][0] if params else None
        if not recv:
            continue
        b0, b1 = mem.params[1], mem.end
        for m in re.finditer(r"(?<![\w$.])" + re.escape(recv) + r"(?=\s*\.\s*apply\s*\()", code[b0:b1]):
            pos = b0 + m.end()
            targets: list = []
            routes: list = []
            for _ in range(8):
                cm = _CONSUMER_CHAIN.match(code, pos, b1)
                if not cm:
                    break
                o = cm.end() - 1
                close = f.close_of(o, b1)
                for s_, e_ in _split(f, o + 1, close):
                    if cm.group(1) == "apply":
                        bare, root = _arg_class(f, s_, e_)
                        tgt, conf, scope = _class_target(f, bare, root)
                        if tgt:
                            targets.append((tgt, conf, scope))
                        elif _value(f, s_, e_)[0] == "id":             # a functional middleware
                            t = _callable(f, s_, e_)
                            if t:
                                targets.append(t)
                    elif cm.group(1) == "forRoutes":
                        routes.extend(_route_specs(f, s_, e_))
                pos = close + 1
            for tgt, conf, scope in targets:
                attrs = {"event": "middleware"}
                if routes:
                    attrs["routes"] = routes[:_MAX_PATHS]
                em.emit(cls.name, tgt, "HOOKS", conf, attrs, scope)
                cls.facts.append("middleware " + tgt)


def _route_specs(f: _File, a: int, b: int) -> list:
    """A forRoutes() argument -> ["path", "path METHOD", "Controller"] (anything unreadable is left out)."""
    v = _value(f, a, b)
    if v[0] == "str":
        return [v[1]]
    if v[0] == "id":
        return [v[1]]
    if v[0] == "obj":
        ents = _entries(f, v[1])
        pv = ents.get("path")
        if not pv:
            return []
        w = _value(f, *pv)
        if w[0] != "str":
            return []
        mv = ents.get("method")
        mw = _value(f, *mv) if mv else ("other",)
        return [f"{w[1]} {mw[1][-1]}" if mw[0] == "member" else w[1]]
    return []


def _hook_edges(f: _File, em: _Emitter, src: str, span, event: str, cls: _Class) -> None:
    for s, e in _deco_args(f, span):
        bare, root = _arg_class(f, s, e)
        v = _value(f, s, e)
        if v[0] == "call":                               # AuthGuard('jwt'): a mixin factory, not a class
            continue
        tgt, conf, scope = _class_target(f, bare, root)
        if tgt:
            em.emit(src, tgt, "HOOKS", conf, {"event": event}, scope)
            cls.facts.append(f"{event} {tgt}")


def _module_edges(f: _File, em: _Emitter, cls: _Class, span) -> None:
    args = _deco_args(f, span)
    if not args:
        return
    v = _value(f, *args[0])
    if v[0] != "obj":
        return
    ents = _entries(f, v[1])
    for role in ("imports", "controllers", "providers", "declarations"):
        sp = ents.get(role)
        if not sp:
            continue
        w = _value(f, *sp)
        if w[0] != "arr":
            continue
        for s, e in _split(f, w[1] + 1, w[2]):
            item = _value(f, s, e)
            attrs = {"via": "module", "role": role}
            if item[0] == "obj":
                pe = _entries(f, item[1])
                uc = pe.get("useClass") or pe.get("useExisting")
                if not uc:
                    continue
                tok = pe.get("provide")
                if tok:
                    tv = _value(f, *tok)
                    attrs["token"] = tv[1] if tv[0] == "str" else _src_text(f, *tok)
                bare, root = _arg_class(f, *uc)
            elif item[0] == "call" and len(item[1]) >= 2:   # UsersModule.forRoot(...): the module itself
                bare, root = item[1][-2], item[1][0]
            else:
                bare, root = _arg_class(f, s, e)
            tgt, _c, scope = _class_target(f, bare, root)
            if tgt and (tgt != cls.name or scope):
                em.emit(cls.name, tgt, "DEPENDS_ON", "NAME", attrs, scope)
                cls.facts.append(f"{role} {tgt}")


def _ctor_edges(f: _File, em: _Emitter, cls: _Class, mem: _Member) -> None:
    for pname, pdecos, ptype, _access in _params(f, *mem.params):
        dd = _deco_map(pdecos)
        attrs = {"via": "ctor"}
        if pname:
            attrs["param"] = pname
        bare = root = ""
        inj = next((d for d in _INJECT_ENTITY_DECOS if d in dd), None)
        if inj:
            args = _deco_args(f, dd[inj])
            if args:
                bare, root = _arg_class(f, *args[0])
                attrs["inject"] = inj
        if not bare:
            bare, root = _type_ref(ptype)
            if "Inject" in dd:
                args = _deco_args(f, dd["Inject"])
                if args:
                    tv = _value(f, *args[0])
                    if tv[0] == "str":
                        attrs["token"] = tv[1]
                    else:
                        tb, tr = _arg_class(f, *args[0])
                        if tb and not tb.isupper():
                            if not bare or bare in _TS_NON_ENTITY:
                                bare, root = tb, tr
                        else:
                            attrs["token"] = _src_text(f, *args[0])
        tgt, _c, scope = _class_target(f, bare, root)
        if tgt and (tgt != cls.name or scope):
            em.emit(cls.name, tgt, "DEPENDS_ON", "NAME", attrs, scope)
            cls.facts.append("injects " + tgt)


def _property_di(f: _File, em: _Emitter, cls: _Class, mem: _Member, md: dict) -> None:
    bare = root = ""
    attrs = {"via": "field", "field": mem.name}
    inj = next((d for d in _INJECT_ENTITY_DECOS if d in md), None)
    if inj:
        args = _deco_args(f, md[inj])
        if args:
            bare, root = _arg_class(f, *args[0])
            attrs["inject"] = inj
    elif "Inject" in md:
        if mem.type_span:
            bare, root = _type_ref(f.code[mem.type_span[0]:mem.type_span[1]])
        args = _deco_args(f, md["Inject"])
        if args:
            tv = _value(f, *args[0])
            if tv[0] == "str":
                attrs["token"] = tv[1]
            elif not bare:
                bare, root = _arg_class(f, *args[0])
    elif mem.init_span:                                  # Angular: private svc = inject(UsersService)
        v = _value(f, *mem.init_span)
        if v[0] == "call" and v[1] == ["inject"]:
            args = _split(f, v[2] + 1, v[3])
            if args:
                bare, root = _arg_class(f, *args[0])
                attrs["via"] = "inject"
    tgt, _c, scope = _class_target(f, bare, root)
    if tgt and (tgt != cls.name or scope):
        em.emit(cls.name, tgt, "DEPENDS_ON", "NAME", attrs, scope)
        cls.facts.append("injects " + tgt)


_COLLECTION_INIT = re.compile(r"new\s+(?:[A-Za-z_$][\w$]*\s*\.\s*)?Collection\s*<\s*(" + _DOTTED.pattern + r")")


def _relation_edges(f: _File, em: _Emitter, cls: _Class, mem: _Member, md: dict, deco: str) -> None:
    """TypeORM / MikroORM relation property -> HAS_MANY / REFERENCES (+ CASCADES)."""
    args = _deco_args(f, md[deco])
    bare = root = ""
    opts = {}
    for s, e in args:
        v = _value(f, s, e)
        if v[0] == "obj":
            opts = _entries(f, v[1])
            ent = opts.get("entity")                    # MikroORM @ManyToOne({ entity: () => Author })
            if ent and not bare:
                bare, root = _arg_class(f, *ent)
        elif not bare and v[0] in ("fn", "id", "member", "str"):
            if v[0] == "str":
                bare, root = v[1], v[1]
            elif v[0] == "fn" and s == args[0][0]:
                bare, root = _arrow_body_class(f, s, e)
            elif v[0] in ("id", "member") and s == args[0][0]:
                bare, root = _arg_class(f, s, e)
    if not bare and mem.type_span:
        bare, root = _element_type(f.code[mem.type_span[0]:mem.type_span[1]])
    if not bare and mem.init_span:                         # MikroORM: tags = new Collection<Tag>(this)
        cm = _COLLECTION_INIT.match(f.code, f.ws(mem.init_span[0], mem.init_span[1]), mem.init_span[1])
        if cm:
            bare, root = _type_ref(cm.group(1))
    tgt, _c, scope = _class_target(f, bare, root)
    if not tgt:
        return
    et = _REL_DECOS[deco]
    attrs = {"decorator": deco, "property": mem.name}
    em.emit(cls.name, tgt, et, "NAME", dict(attrs), scope)
    cls.facts.append(f"{deco} {mem.name}")
    # ORM-level remove cascade: owner -> element (JPA CascadeType.REMOVE shape)
    cv = opts.get("cascade")
    if cv:
        w = _value(f, *cv)
        strategy = None
        if f.code[cv[0]:cv[1]].strip() == "true":           # TypeORM: true = every cascade, remove included
            strategy = "remove"
        elif w[0] == "arr":
            vals = []
            for s, e in _split(f, w[1] + 1, w[2]):
                x = _value(f, s, e)
                if x[0] == "str":
                    vals.append(x[1])
                elif x[0] == "member" and x[1][-1].upper() in ("REMOVE", "ALL"):
                    vals.append("remove")           # MikroORM Cascade.REMOVE / Cascade.ALL
            hit = [x for x in vals if x in ("remove", "soft-remove")]
            strategy = hit[0] if hit else None
        elif w[0] == "member" and w[1][-1].upper() in ("REMOVE", "ALL"):
            strategy = "remove"
        if strategy:
            em.emit(cls.name, tgt, "CASCADES", "NAME",
                    {"strategy": strategy, "via": _orm_of(f, deco), "property": mem.name}, scope)
            cls.facts.append(f"cascade {cls.name} -> {tgt}")
    # DB foreign-key cascade: the referenced entity is the principal (a belongs-to)
    od = opts.get("onDelete") or opts.get("deleteRule")
    if od and deco in ("ManyToOne", "OneToOne"):
        w = _value(f, *od)
        rule = (w[1] if w[0] == "str" else "").strip().upper()
        owner = opts.get("owner")
        holds_key = deco == "ManyToOne" or "JoinColumn" in md \
            or (owner is not None and f.code[owner[0]:owner[1]].strip() == "true")
        if rule in ("CASCADE", "SET NULL") and holds_key:
            em.emit(tgt, cls.name, "CASCADES", "NAME",
                    {"strategy": rule, "via": _orm_of(f, deco), "property": mem.name}, scope)
            cls.facts.append(f"cascade {tgt} -> {cls.name}")


# ---- Express-style routers ------------------------------------------------------------------------
_RECV_VERB = re.compile(r"\.\s*(get|post|put|patch|delete|del|all|head|options|use|route|register|on)\s*"
                        r"(?:<[^()\n]{0,400}?>)?\s*\(")
_CHAIN_CALL = re.compile(r"\s*\.\s*(get|post|put|patch|delete|del|all|head|options|route)\s*"
                         r"(?:<[^()\n]{0,400}?>)?\s*\(")
_ROUTER_PARAM = re.compile(
    r"(?:function\s*[\w$]*\s*\(|\(|,)\s*(app|router|server|fastify|instance)\s*(?::\s*[\w$.<>]+\s*)?[,)]")
_ROUTER_TYPE_ANN = re.compile(          # `<name>: Express` — the name is read backwards from the colon
    r":\s*(?:Express|Application|Router|express\.Router|express\.Application|FastifyInstance|Hono|KoaRouter)\b")
_REGISTER_CALL = re.compile(r"\.\s*register\s*\(")
_NEST_CREATE = r"\s*\.\s*create\s*[<(]"   # an HTTP app; createApplicationContext / createMicroservice serve no routes


def _nest_bootstrap(f: _File) -> bool:
    """The file starts a NestJS HTTP app: `NestFactory.create(` — also through an alias (`import { NestFactory as
    NF } from '@nestjs/core'`). A seed / CLI script's createApplicationContext or a createMicroservice worker is
    no app root."""
    names = {"NestFactory"} | {k for k, (spec, imp) in f.imports.items()
                               if imp == "NestFactory" and spec == "@nestjs/core"}
    code = f.code
    for m in re.finditer("(?:" + "|".join(re.escape(n) for n in sorted(names)) + ")" + _NEST_CREATE, code):
        if not (m.start() and (code[m.start() - 1].isalnum() or code[m.start() - 1] in "_$")):
            return True
    return False


def _inline_name(offset: int) -> str:
    """The plugin-function name of a function literal registered inline at `offset` (never a symbol)."""
    return f"<register@{offset}>"


def _fn_literal(f: _File, a: int, b: int):
    """A function literal spanning code[a:b] — `(x, opts) => {...}`, `async (x) => {...}`, `x => {...}`,
    `async function (x) {...}` — -> (its first parameter, body open, body close); None for anything else, a
    function with no parameter, or an expression body."""
    code = f.code
    j = f.ws(a, b)
    am = _ASYNC_RE.match(code, j, b)
    if am and not code.startswith("=>", am.end()):
        j = am.end()
    fm = _FN_HEAD.match(code, j, b)
    if fm:
        o = code.find("(", fm.end(), b)
    elif j < b and code[j] == "(":
        o = j
    else:
        im = _IDENT.match(code, j, b)
        k = f.ws(im.end(), b) if im else b
        if not im or not code.startswith("=>", k):
            return None
        k = f.ws(k + 2, b)
        return (im.group(0), k, f.close_of(k, b)) if k < b and code[k] == "{" else None
    if o < 0:
        return None
    first = _IDENT.match(code, f.ws(o + 1, b), b)
    body = _fn_body(f, o, b) if first else None
    return (first.group(0), body[0], body[1]) if body else None


class _RouterFacts:
    """The router side of one file: router receivers (variables built by a framework constructor, or a
    conventional app/router/fastify parameter in a file that uses an HTTP framework), the routes declared on
    them and the mounts that compose their paths. emit_routes() emits this file's ROUTES_TO in declaration
    order; finalize() rebuilds the same facts to compose paths across files."""

    def __init__(self, f: _File):
        self.f = f
        self.routers: dict = {}      # var -> (framework, own prefix | None)
        self.fns: dict = {}          # plugin function name -> (first parameter, body open, body close)
        self.routes: list = []       # (owner node, raw path list | None, dst, conf, attrs)
        self.mounts: list = []       # (parent node, child ref, prefix | None, framework)
        self.globals: list = []      # NestJS app.setGlobalPrefix literals (None: unreadable)
        # A NestJS bootstrap file (NestFactory.create...): its directory is an app's root (finalize).
        self.bootstrap = "NestFactory" in f.code and _nest_bootstrap(f)
        self.framework = next((_FRAMEWORK_SPECS[spec] for spec, _imp in f.imports.values()
                               if spec in _FRAMEWORK_SPECS), None)
        self._param_receivers = None
        self._typed = None
        self._fn_index = None        # parameter -> (bodies sorted by start, starts, enclosing body index)
        self._foreach = _foreach_sites(f)
        self._find_routers()
        self._find_plugin_fns()
        self._index_plugin_fns()
        self._scan_calls()

    # -- receivers ---------------------------------------------------------------------------------
    def _ctor_framework(self, a: int, b: int):
        """(framework, own prefix) when code[a:b] builds a router / app, else None."""
        f = self.f
        code = f.code
        v = _value(f, a, b)
        prefix = None
        if v[0] in ("chain", "newchain"):               # new Hono().basePath('/api'), require('x')()
            bm = re.compile(r"\.\s*basePath\s*\(").match(code, v[4], b)
            if bm:
                o = bm.end() - 1
                args = _split(f, o + 1, f.close_of(o, b))
                if args:
                    prefix = _lit_str(f, *args[0])
            base = ("new" if v[0] == "newchain" else "call", v[1])
        else:
            base = v
        if base[0] not in ("call", "new"):
            return None
        parts = base[1]
        root, last = parts[0], parts[-1]
        fw = _FRAMEWORK_SPECS.get(f.import_spec(root) or "")
        if fw is None and root == "require":
            rm = re.compile(r"require\s*\(\s*(['\"])").match(code, f.ws(a, b), b)
            if rm:
                fw = _FRAMEWORK_SPECS.get(f.lit(rm.start(1)) or "")
        if fw is None and last in ("Hono", "OpenAPIHono") and base[0] == "new":
            fw = "hono"
        if fw is None:
            return None
        if fw == "koa" and base[0] == "new" and last != "Koa":
            prefix = self._koa_prefix(a, b)
        return fw, prefix

    def _koa_prefix(self, a: int, b: int):
        f = self.f
        o = f.code.find("(", a, b)
        if o < 0:
            return None
        args = _split(f, o + 1, f.close_of(o, b))
        if args:
            w = _value(f, *args[0])
            if w[0] == "obj":
                pv = _entries(f, w[1]).get("prefix")
                if pv:
                    return _lit_str(f, *pv)
        return None

    def _find_routers(self):
        f = self.f
        for name, (a, b) in f.vars.items():
            got = self._ctor_framework(a, b)
            if got:
                self.routers[name] = got
        for m in _THIS_ASSIGN.finditer(f.code):
            name = "this." + m.group(1)
            if name in self.routers:
                continue
            got = self._ctor_framework(m.end(), _expr_end(f, m.end(), len(f.code)))
            if got:
                self.routers[name] = got
        for cls in f.classes.values():                  # class Ctl { router = Router(); ... }
            for mem in cls.members:
                if mem.kind == "prop" and mem.name and mem.init_span and "this." + mem.name not in self.routers:
                    got = self._ctor_framework(*mem.init_span)
                    if got:
                        self.routers["this." + mem.name] = got

    def _find_plugin_fns(self):
        """Functions whose first parameter is a conventional receiver, or one typed with a framework app /
        router type: a Fastify plugin `async function routes(fastify, opts) {...}` / `(srv: FastifyInstance)`,
        an Express `module.exports = function (app) {...}` (anonymous default exports are named "<default>")."""
        f = self.f
        code = f.code
        n = len(code)
        cands = [(name, code.find("(", pos, min(n, pos + 400))) for name, pos in f.funcs.items()]
        for m in _ANON_DEFAULT_FN.finditer(code):
            o = code.find("(", m.start(), min(n, m.end() + 40))
            if o >= 0:
                cands.append(("<default>", o))
        for name, o in cands:
            if o < 0 or code[o] != "(":
                continue
            first = _IDENT.match(code, f.ws(o + 1, n))
            if not first:
                continue
            if first.group(0) not in _CONVENTIONAL_RECEIVERS:      # else only a framework-typed one: (srv: Hono)
                params = _params(f, o, f.close_of(o, n)) if self.typed_receivers() else []
                if not params or params[0][0] != first.group(0) or not _router_type(f, params[0][2]):
                    continue
            body = _fn_body(f, o, n)
            if body is None:
                continue
            self.fns.setdefault(name, (first.group(0), body[0], body[1]))
        if "<default>" in self.fns:
            f.exports.setdefault("default", "<default>")
            if _MODULE_EXPORTS.search(code):
                f.exports.setdefault("=", "<default>")
        # A plugin passed inline to a Fastify register(): `server.register((instance, opts) => {...}, { prefix })`.
        # Its first parameter is the encapsulated instance whatever its name; read in source order, so a
        # register() inside such a plugin sees its parent.
        for m in (_REGISTER_CALL.finditer(code) if "register" in code else ()):
            rb = _receiver_before(code, m.start(), allow_this=True)
            if rb is None or rb[1] == "this":
                continue
            got = self._receiver(*rb)
            if not got or got[1] != "fastify":
                continue
            o = m.end() - 1
            args = _split(f, o + 1, f.close_of(o, n))
            lit = _fn_literal(f, *args[0]) if args else None
            if lit is not None:
                self.fns.setdefault(_inline_name(args[0][0]), lit)

    def _receiver(self, this: bool, name: str, pos: int):
        """(owner node, framework) for a call receiver, or None when it is not a router."""
        f = self.f
        key = ("this." + name) if this else name
        if key in self.routers:
            return ("var", key), self.routers[key][0]
        typed = self.typed_receivers().get(key)
        if this:
            return (("var", key), typed) if typed else None
        if name in f.vars and name not in self.routers and not typed:
            return None                                  # bound here to something that is no router
        fw = self.framework
        fn = self._enclosing_plugin(name, pos)
        if fn:
            return ("fn", fn), typed or fw or ("fastify" if name in ("fastify", "instance") else "express")
        if typed:
            return ("var", name), typed
        if not (name in _CONVENTIONAL_RECEIVERS or name.lower().endswith("router")):
            return None
        if name in f.imports:
            if is_external(f.imports[name][0]):
                return None
            return ("var", name), fw or "express"
        if fw or name in self.param_receivers():
            return ("var", name), fw or "express"
        return None

    def param_receivers(self) -> set:
        """Names a function parameter list or a framework type annotation shows as an app / router."""
        if self._param_receivers is None:
            code = self.f.code
            got = {m.group(1) for m in _ROUTER_PARAM.finditer(code)}
            for m in _ROUTER_TYPE_ANN.finditer(code):
                rb = _receiver_before(code, m.start(), dot=":")
                if rb is not None:
                    got.add(rb[1])
            self._param_receivers = got
        return self._param_receivers

    def typed_receivers(self) -> dict:
        """{name: framework} for names a framework app / router TYPE annotates, whatever they are called: a
        parameter or variable (`r: Router`, `srv: FastifyInstance`, `api: express.Router`) by its name, a
        class field or constructor parameter property as `this.<name>`. Never an array (`Router[]`)."""
        if self._typed is None:
            f = self.f
            got: dict = {}
            roots = [local for local, (spec, _imp) in f.imports.items() if spec in _FRAMEWORK_SPECS]
            if roots:
                code = f.code
                alt = "|".join(re.escape(r) for r in sorted(roots, key=len, reverse=True))
                ann = re.compile(r":\s*((?:" + alt + r")(?:\s*\.\s*[A-Za-z_$][\w$]*)?)(?![\w$])")
                for m in ann.finditer(code):
                    fw = _router_type(f, m.group(1))
                    if not fw or code.startswith(("[", "("), f.ws(m.end(), len(code))):
                        continue                         # an array type, or a call (`c ? a : Router()`)
                    k = m.start() - 1
                    while k >= 0 and code[k] in _WS_CHARS:
                        k -= 1
                    rb = _receiver_before(code, k, dot=code[k]) if k >= 0 and code[k] in "?!" \
                        else _receiver_before(code, m.start(), dot=":")
                    if rb is not None:
                        got.setdefault(rb[1], fw)
                for cls in f.classes.values():
                    for mem in cls.members:
                        if mem.kind == "prop" and mem.name and mem.type_span:
                            fw = _router_type(f, code[mem.type_span[0]:mem.type_span[1]])
                            if fw:
                                got.setdefault("this." + mem.name, fw)
                        elif mem.kind == "method" and mem.name == "constructor" and mem.params:
                            for pname, _d, ptype, access in _params(f, *mem.params):
                                fw = _router_type(f, ptype) if (access and pname) else None
                                if fw:
                                    got.setdefault("this." + pname, fw)
            self._typed = got
        return self._typed

    def _index_plugin_fns(self):
        """Per first-parameter name, the plugin functions' bodies sorted by their `{` with the index of each
        one's innermost enclosing body (bodies nest or are disjoint), so a lookup is a bisect plus a walk up
        that chain instead of a scan of every function."""
        idx: dict = {}
        for fn, (param, b0, b1) in self.fns.items():
            idx.setdefault(param, []).append((b0, b1, fn))
        for param, spans in idx.items():
            spans.sort()
            parents, stack = [], []
            for i, (b0, _b1, _fn) in enumerate(spans):
                while stack and spans[stack[-1]][1] < b0:
                    stack.pop()
                parents.append(stack[-1] if stack else -1)
                stack.append(i)
            idx[param] = (spans, [s[0] for s in spans], parents)
        self._fn_index = idx

    def _enclosing_plugin(self, name: str, pos: int):
        """The innermost plugin function whose first parameter is `name` and whose body holds `pos`."""
        if self._fn_index is None:                       # while the plugin functions are still being found
            best, best_open = None, -1
            for fn, (param, b0, b1) in self.fns.items():
                if param == name and b0 < pos < b1 and b0 > best_open:
                    best, best_open = fn, b0
            return best
        got = self._fn_index.get(name)
        if not got:
            return None
        spans, starts, parents = got
        i = bisect.bisect_left(starts, pos) - 1          # the last body opening before pos
        while i >= 0:
            b0, b1, fn = spans[i]
            if b0 < pos < b1:
                return fn
            i = parents[i]
        return None

    # -- calls -------------------------------------------------------------------------------------
    def _scan_calls(self):
        f = self.f
        code = f.code
        n = len(code)
        for m in _RECV_VERB.finditer(code):
            rb = _receiver_before(code, m.start(), allow_this=True)
            if rb is None or rb[1] == "this":
                continue
            this, name, start = rb
            verb = m.group(1)
            got = self._receiver(this, name, start)
            if not got:
                continue
            node, fw = got
            o = m.end() - 1
            close = f.close_of(o, n)
            if verb in ("use", "register"):
                self._mount(node, fw, o, close, verb)
            elif verb == "route":
                args = _split(f, o + 1, close)
                if len(args) == 1 and _value(f, *args[0])[0] == "obj":
                    self._route_object(node, fw, args[0])
                elif len(args) == 1:
                    paths = _str_list(f, *args[0])
                    if paths and all(_is_route_path(p) for p in paths):
                        self._chain(node, fw, close, paths)
                elif len(args) == 2:                     # Hono app.route('/books', books)
                    self._mount(node, fw, o, close, "route")
            elif verb == "on":
                if fw == "hono":
                    self._hono_on(node, fw, o, close)
            else:
                self._route_call(node, fw, verb, o, close)
                self._chain(node, fw, close, None)
        for m in (re.finditer(r"\.\s*setGlobalPrefix\s*\(", code) if "setGlobalPrefix" in code else ()):
            o = m.end() - 1
            args = _split(f, o + 1, f.close_of(o, n))
            if not args:
                continue
            # a literal, or a const in scope (`const globalPrefix = 'api'`); an exclude list -> unreadable
            self.globals.append(None if len(args) > 1 else _lit_str(f, *args[0]))

    def _chain(self, node, fw, close, paths):
        """`.get(h).post(h2)` following a route call or `.route(path)` on the same receiver."""
        f = self.f
        code = f.code
        n = len(code)
        pos = close + 1
        for _ in range(32):
            cm = _CHAIN_CALL.match(code, pos)
            if not cm or cm.group(1) == "route":
                return
            o = cm.end() - 1
            c2 = f.close_of(o, n)
            self._route_call(node, fw, cm.group(1), o, c2, chain_paths=paths)
            pos = c2 + 1

    def _route_call(self, node, fw, verb, o, close, chain_paths=None):
        f = self.f
        args = _split(f, o + 1, close)
        if not args:
            return
        attrs: dict = {"method": _ROUTE_VERBS[verb]}
        if chain_paths is not None:
            paths, rest = chain_paths, args
        else:
            first = _str_list(f, *args[0])
            if first is not None and len(args) >= 2 and fw == "koa":
                second = _str_list(f, *args[1])          # a koa NAMED route: router.get('user', '/u/:id', h)
                if second is not None and first and not _is_route_path(first[0]) \
                        and all(_is_route_path(p) for p in second):
                    attrs["name"] = first[0]
                    first, args = second, args[1:]
            if first is None:
                # A non-literal path is still a route on a router this file BUILT; never on a guessed one.
                if node[0] != "var" or node[1] not in self.routers or len(args) < 2 \
                        or _value(f, *args[0])[0] in ("fn", "obj", "arr"):
                    return
                attrs["path_unresolved"] = _src_text(f, *args[0])
                paths = None
            elif not first or not all(_is_route_path(p) for p in first):
                return
            else:
                paths = first
            rest = args[1:]
        if rest:
            self._emit_handlers(node, fw, paths, rest, attrs)

    def _emit_handlers(self, node, fw, paths, rest, attrs):
        f = self.f
        handlers: list = []
        middleware: list = []
        for s, e in rest:
            v = _value(f, s, e)
            if v[0] == "arr":
                handlers.extend(_split(f, v[1] + 1, v[2]))
            elif v[0] == "obj":                          # fastify route options
                ents = _entries(f, v[1])
                for hook in ("onRequest", "preParsing", "preValidation", "preHandler"):
                    hv = ents.get(hook)
                    if hv:
                        w = _value(f, *hv)
                        middleware.extend(_split(f, w[1] + 1, w[2]) if w[0] == "arr" else [hv])
                if ents.get("handler"):
                    handlers.append(ents["handler"])
            else:
                handlers.append((s, e))
        if not handlers:
            return
        *mids, final = handlers
        target = _callable(f, *final, final=True)
        if target is None:
            return
        names = [t[0] for t in (_callable(f, s, e) for s, e in middleware + mids) if t]
        if names:
            attrs["middleware"] = list(dict.fromkeys(names))[:8]
        attrs["framework"] = fw
        self.routes.append((node, list(paths) if paths is not None else None, target[0], target[1], attrs,
                            target[2]))

    def _route_object(self, node, fw, arg):
        """fastify.route({ method, url, handler, preHandler })."""
        f = self.f
        v = _value(f, *arg)
        ents = _entries(f, v[1])
        url = ents.get("url") or ents.get("path")
        if not url or not ents.get("handler"):
            return
        paths = _str_list(f, *url)
        if not paths or not all(_is_route_path(p) for p in paths):
            return
        methods = ["*"]
        mv = ents.get("method")
        if mv:
            ml = _str_list(f, *mv)
            if ml:
                methods = [x.upper() for x in ml]
        self._emit_handlers(node, fw, paths, [arg], {"method": ",".join(methods)})

    def _hono_on(self, node, fw, o, close):
        f = self.f
        args = _split(f, o + 1, close)
        if len(args) < 3:
            return
        methods = _str_list(f, *args[0])
        paths = _str_list(f, *args[1])
        if not methods or not paths or not all(_is_route_path(p) for p in paths):
            return
        self._emit_handlers(node, fw, paths, args[2:], {"method": ",".join(x.upper() for x in methods)})

    def _mount(self, node, fw, o, close, kind):
        f = self.f
        args = _split(f, o + 1, close)
        if not args:
            return
        if kind == "register":                           # fastify.register(plugin, { prefix })
            name = _inline_name(args[0][0])
            child = ("local", name) if name in self.fns else _mount_ref(f, *args[0])
            prefix = ""
            if len(args) >= 2:
                w = _value(f, *args[1])
                if w[0] == "obj":
                    pv = _entries(f, w[1]).get("prefix")
                    if pv:
                        prefix = _lit_str(f, *pv)
            if child is not None:
                self.mounts.append((node, child, prefix, fw))
            return
        first = _value(f, *args[0])
        if len(args) >= 2 and self._foreach_mounts(node, fw, o, first, _value(f, *args[-1])):
            return
        if first[0] in ("id", "member"):                 # app.use(API_PREFIX, router): a const's literal
            s = _ref_str(f, first, args[0][0])
            first = ("str", s) if s is not None else first
        if len(args) == 1:
            prefix, operands = "", args
        elif first[0] == "str":
            if not _is_route_path(first[1]):
                return
            prefix, operands = first[1], args[1:]
        elif first[0] in ("dyn", "id", "member"):
            prefix, operands = None, args[1:]            # app.use(config.prefix, router): no literal
        else:
            return
        # Every router among the operands is mounted there (`app.use('/v1', auth, api)`, koa's
        # `use('/users', users.routes(), users.allowedMethods())`); a middleware function, an
        # allowedMethods() call or anything unknown names no router and adds nothing.
        for s, e in operands:
            child = _mount_ref(f, s, e)
            if child is not None:
                self.mounts.append((node, child, prefix, fw))

    def _foreach_mounts(self, node, fw, pos, first, last) -> bool:
        """`routes.forEach((r) => router.use(r.path, r.route))` (or `({ path, route }) => router.use(path, route)`)
        over a LITERAL array of `{ path: '/x', route: xRouter }` declared in this file: one mount per element.
        True when the call was that idiom (mounted or not), so it is never read as one dynamic mount."""
        f = self.f
        for arr, param, keys, b0, b1 in self._foreach:
            if not (b0 < pos < b1):
                continue
            if (param and first[0] == "member" and last[0] == "member" and len(first[1]) == 2
                    and first[1][0] == param and last[1][0] == param):
                kp, kr = first[1][1], last[1][1]
            elif keys and first[0] == "id" and last[0] == "id" and first[1] in keys and last[1] in keys:
                kp, kr = keys[first[1]], keys[last[1]]
            else:
                continue
            span = f.vars.get(arr)
            v = _value(f, *span) if span else ("other",)
            if v[0] != "arr":
                return True
            for s, e in _split(f, v[1] + 1, v[2]):
                item = _value(f, s, e)
                if item[0] != "obj":
                    continue
                ents = _entries(f, item[1])
                pv, rv = ents.get(kp), ents.get(kr)
                if not pv or not rv:
                    continue
                pw = _lit_str(f, *pv)
                prefix = pw if pw is not None and _is_route_path(pw) else None
                child = _mount_ref(f, *rv)
                if child is not None:
                    self.mounts.append((node, child, prefix, fw))
            return True
        return False

    # -- emission ----------------------------------------------------------------------------------
    def emit_routes(self, em: _Emitter, world) -> None:
        f = self.f
        for node, paths, dst, conf, attrs, scope in self.routes:
            a = dict(attrs)
            if paths is not None:
                a = _composed_attrs(a, world.route_paths(f.rel, node, paths))
            em.emit(f.stem, dst, "ROUTES_TO", conf, a, scope)


_FOREACH = re.compile(r"\.\s*forEach\s*\(")


def _foreach_sites(f: _File) -> list:
    """[(array var, callback parameter | "", {destructured local: key}, body open, body close)] for every
    `arr.forEach((r) => ...)` / `arr.forEach(function (r) {...})` / `arr.forEach(({ path, route }) => ...)`."""
    code = f.code
    n = len(code)
    out = []
    for m in (_FOREACH.finditer(code) if "forEach" in code else ()):
        rb = _receiver_before(code, m.start())
        if rb is None:
            continue
        o = m.end() - 1
        close = f.close_of(o, n)
        j = f.ws(o + 1, close)
        fm = _FN_HEAD.match(code, j, close)
        if fm:
            j = code.find("(", fm.end(), close)
            if j < 0:
                continue
        param, keys = "", {}
        if j < close and code[j] == "(":
            pc = f.close_of(j, close)
            k = f.ws(j + 1, pc)
            if k < pc and code[k] == "{":
                for seg_s, seg_e in _split(f, k + 1, f.close_of(k, pc)):
                    parts = [x.strip() for x in code[seg_s:seg_e].split(":")]
                    if len(parts) == 1 and _IDENT.fullmatch(parts[0]):
                        keys[parts[0]] = parts[0]
                    elif len(parts) == 2 and _IDENT.fullmatch(parts[0]) and _IDENT.fullmatch(parts[1]):
                        keys[parts[1]] = parts[0]
            else:
                im = _IDENT.match(code, k, pc)
                param = im.group(0) if im else ""
        else:
            im = _IDENT.match(code, j, close)
            param = im.group(0) if im else ""
        if param or keys:
            out.append((rb[1], param, keys, o, close))
    return out


def _receiver_before(code: str, pos: int, allow_this: bool = False, dot: str = "."):
    """(after `this.`, name, start) of the identifier that ends, whitespace aside, right before code[pos] (a
    `dot`), or None: a call result, a subscript, a member of something else (`a.b.get(` - unless `this.b` and
    `allow_this`). The scan goes back over that identifier and its whitespace only."""
    if pos >= len(code) or code[pos] != dot:
        return None
    j = pos - 1
    while j >= 0 and code[j] in _WS_CHARS:
        j -= 1
    end = j + 1
    while j >= 0 and (code[j].isalnum() or code[j] in "_$"):
        j -= 1
    start = j + 1
    if start >= end or code[start].isdigit():
        return None
    k = j
    while k >= 0 and code[k] in _WS_CHARS:
        k -= 1
    if k < 0 or code[k] != ".":
        return False, code[start:end], start
    if not allow_this:
        return None
    t = k - 1
    while t >= 0 and code[t] in _WS_CHARS:
        t -= 1
    t_end = t + 1
    while t >= 0 and (code[t].isalnum() or code[t] in "_$"):
        t -= 1
    if code[t + 1:t_end] == "this" and (t < 0 or not (code[t].isalnum() or code[t] in "_$.")):
        return True, code[start:end], t + 1
    return None


def _fn_body(f: _File, o: int, end: int):
    """(open, close) of the block body of the function whose parameter list opens at `o`, or None (an
    expression-bodied arrow, a missing body)."""
    code = f.code
    pc = f.close_of(o, end)
    j = f.ws(pc + 1, end)
    if j < end and code[j] == ":":                      # a return type annotation
        k = code.find("=>", j, min(end, j + 400))
        b = code.find("{", j, min(end, j + 400))
        if k >= 0 and (b < 0 or k < b):
            j = k
        elif b >= 0:
            return (b, f.close_of(b, end))
    if code.startswith("=>", j):
        j = f.ws(j + 2, end)
    if j < end and code[j] == "{":
        return (j, f.close_of(j, end))
    return None


def _is_route_path(p) -> bool:
    return isinstance(p, str) and (p == "" or p.startswith("/") or p in ("*", "(.*)"))


def _mount_ref(f: _File, a: int, b: int):
    """A mount operand -> ('local', name) | ('import', spec, imported) | None."""
    v = _value(f, a, b)
    if v[0] == "call" and v[1] == ["require"]:
        args = _split(f, v[2] + 1, v[3])
        if args:
            w = _value(f, *args[0])
            if w[0] == "str":
                return ("import", w[1], "=")
        return None
    if v[0] == "call" and len(v[1]) == 2 and v[1][1] in ("routes", "middleware"):   # koa router.routes()
        v = ("id", v[1][0])
    if v[0] == "chain" and v[1] == ["require"]:
        return None
    if v[0] == "id":
        name = v[1]
        if name in f.imports:                            # finalize places a bare specifier (baseUrl, paths, a
            spec, imported = f.imports[name]             # workspace package) or finds it is a package
            return None if is_package(spec) else ("import", spec, imported)
        return ("local", name)
    if v[0] == "member" and len(v[1]) == 2 and v[1][0] == "this":
        return ("local", "this." + v[1][1])
    return None


def _callable(f: _File, a: int, b: int, final: bool = False):
    """A handler expression -> (dst name, conf hint, scope), or None when it names no symbol of this repo.
    A function of this file is LOCAL; an imported one NAME (its exported name); `this.m` inside a class,
    `X.m` on a class, `x.m` on `x = new X()` and `this.x.m` on a field of class X are `X.m`; `m.f` on an
    imported MODULE OBJECT (a namespace,
    default or require() import) is `f` resolved inside that module only (a module scope), never by name
    across the repo. A final handler wrapped once (`asyncHandler(fn)`) is unwrapped, graded at most NAME."""
    v = _value(f, a, b)
    kind = v[0]
    if kind == "id":
        name = v[1]
        if name in f.funcs and f.fn_visible(name, a):
            return name, "LOCAL", ""
        if name in f.imports:                            # a helper nested elsewhere never shadows the import
            spec, imported = f.imports[name]
            if is_package(spec):
                return None
            return ((name if imported in ("default", "*", "=") else imported), "NAME",
                    _pref_scope(f, spec, "default" if imported in ("default", "=") else ""))
        return None
    if kind == "member":
        return _member_callable(f, v[1], a)
    if kind == "call" and v[1][-1] == "bind" and len(v[1]) >= 2:          # ctrl.list.bind(ctrl)
        return _member_callable(f, v[1][:-1], a)
    if final and kind == "call" and len(v[1]) == 1 and _WRAPPER_NAME.match(v[1][0]):
        args = _split(f, v[2] + 1, v[3])
        if args:
            inner = _callable(f, *args[0])
            if inner:
                return inner[0], "NAME", inner[2]
    return None


def _member_callable(f: _File, parts: list, pos: int):
    if len(parts) == 3 and parts[0] == "this":          # this.users.list: the class of the `users` field
        cls = _enclosing_class(f, pos)
        tgt, conf, scope = _field_class(f, cls, parts[1]) if cls else ("", "", "")
        return (f"{tgt}.{parts[2]}", conf, scope) if tgt else None
    if len(parts) != 2:
        return None
    root, m = parts
    if root == "this":
        cls = _enclosing_class(f, pos)
        return (f"{cls.name}.{m}", "LOCAL", "") if cls else None
    if root in f.classes:
        return f"{root}.{m}", "LOCAL", ""
    if root in f.new_vars:                               # x = new X(): X's import names its module
        ctor = f.new_vars[root]
        tgt, conf, scope = _class_target(f, ctor.rsplit(".", 1)[-1], ctor.split(".", 1)[0])
        return (f"{tgt}.{m}", conf, scope) if tgt else None
    if root in f.imports:
        spec, imported = f.imports[root]
        if is_package(spec):
            return None
        if imported not in ("default", "*", "=") and imported[:1].isupper():
            return f"{imported}.{m}", "NAME", _pref_scope(f, spec)     # an imported class: its static method
        scope = import_scope(f.rel, spec)
        if not scope:
            return None
        # what finalize follows: a namespace's export `m` ("#*"), or the object a named import binds (`#user`
        # for `import { user } from './controller'`, a barrel's `export { default as user } from './user'`)
        note = "#*" if imported == "*" else ("" if imported in ("default", "=") else "#" + imported)
        return m, "NAME", scope + note
    return None


def _pref_scope(f: _File, spec: str, note: str = "") -> str:
    """"pref:<import scope>" for a name imported by `spec` (edge_resolve prefers that module's own symbol;
    finalize places it exactly, or drops the edge). `note` tells finalize which export it is when the edge's
    name does not say: "default" for a default import (its local name is not the symbol's), or the imported
    binding a dotted type reference goes through (`ns.Base` with `import { ns }`)."""
    s = import_scope(f.rel, spec)
    return ("pref:" + s + ("#" + note if note else "")) if s else ""


_MODULE_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


def _module_key(path: str) -> str:
    """A module path the way edge_resolve keys a file: no extension, and an index file names its directory."""
    p = path
    for ext in _MODULE_EXTS:
        if p.lower().endswith(ext):
            p = p[: -len(ext)]
            break
    if p == "index":
        return ""
    return p[: -len("/index")] if p.endswith("/index") else p


def module_scope(rel: str, spec: str) -> str:
    """The edge_resolve scope of a module imported by `spec` in file `rel`: "module:<path>" for a relative
    import (the root-relative module path), "modsuffix:<path>" for an alias (`@/x`, `~/x`, `#x`, `src/x`) — a
    guess finalize falls back to when no tsconfig maps the alias —, "" when the module cannot be located from
    here (a workspace package, a path above the root)."""
    s = str(spec or "").split("?", 1)[0]
    if s.startswith("."):
        p = posixpath.normpath(posixpath.join(posixpath.dirname(rel), s))
        if p == ".." or p.startswith("../"):
            return ""
        key = _module_key("" if p == "." else p)
        return "module:" + key if key else ""
    for pre in ("@/", "~/", "#/", "@src/", "~", "#"):
        if s.startswith(pre):
            key = _module_key(s[len(pre):].lstrip("/"))
            return "modsuffix:" + key if key else ""
    if s.startswith(("src/", "app/")):
        key = _module_key(s)
        return "modsuffix:" + key if key else ""
    return ""


def import_scope(rel: str, spec: str) -> str:
    """The scope the reader gives a name imported by `spec` in file `rel`: "module:<path>" for a relative import
    (exact, module_scope), "spec:<specifier>" for any other — an alias, a tsconfig `baseUrl` / `paths` name, a
    Node.js subpath import (`#src/x`), a workspace package, an npm package — which finalize resolves against
    the tree (an exact module, an alias guess) or drops (a package the tree does not hold); "" for a path above
    the root. The specifier's own `%` and `#` are percent-encoded: a `#` after it starts finalize's note."""
    s = str(spec or "")
    if s.startswith("."):
        return module_scope(rel, s)
    return "spec:" + s.replace("%", "%25").replace("#", "%23") if s else ""


def _scope_spec(arg: str) -> str:
    """The specifier a "spec:" scope encodes (import_scope)."""
    return arg.replace("%23", "#").replace("%25", "%")


def _enclosing_class(f: _File, pos: int):
    """The innermost class (not an interface) whose body holds `pos`, or None. The class bodies are indexed
    once, sorted, each with its enclosing body (they nest or are disjoint), so a lookup is a bisect plus a walk
    up that chain, never a scan of every class."""
    if f.class_index is None:
        spans = sorted((c.body, c.end, k, c) for k, c in enumerate(f.classes.values()) if not c.iface)
        parents, stack = [], []
        for k, (b0, _b1, _i, _c) in enumerate(spans):
            while stack and spans[stack[-1]][1] < b0:
                stack.pop()
            parents.append(stack[-1] if stack else -1)
            stack.append(k)
        f.class_index = (spans, [s[0] for s in spans], parents)
    spans, starts, parents = f.class_index
    k = bisect.bisect_left(starts, pos) - 1              # the last class body opening before pos
    while k >= 0:
        b0, b1, _i, cls = spans[k]
        if b0 < pos < b1:
            return cls
        k = parents[k]
    return None


def _composed_attrs(attrs: dict, composed: list) -> dict:
    """Rewrite a route's path attrs from its composed [(path, unresolved)] (framework_edges' shape: `path` =
    the first, `paths` when several, `prefix_unresolved` naming what was left out)."""
    paths = sorted({p for p, _u in composed if p is not None})[:_MAX_PATHS]
    unres = sorted({x for _p, u in composed for x in u})
    new = dict(attrs)
    if paths:
        new["path"] = paths[0]
    else:
        new.pop("path", None)
    if len(paths) > 1:
        new["paths"] = paths
    else:
        new.pop("paths", None)
    if unres:
        new["prefix_unresolved"] = ", ".join(unres)
    else:
        new.pop("prefix_unresolved", None)
    return new


def _file_for(files, path: str):
    """The file of `files` a module path names, as Node / TypeScript look it up: the path itself when it has a
    source extension (a NodeNext `./users.js` naming users.ts too), then with each extension, then its index
    file. None when none is there (or the path leaves the root)."""
    p = posixpath.normpath(path) if path else ""
    if p == "." or not p:
        p = ""
    elif p == ".." or p.startswith("../"):
        return None
    cands = []
    if p:
        stem, ext = posixpath.splitext(p)
        if ext.lower() in _NEXT_FILE_EXTS:
            cands.append(p)
        cands += [stem + twin for twin in _TS_TWINS.get(ext.lower(), ())]   # NodeNext: './users.js' -> users.ts
        cands += [p + e for e in _NEXT_FILE_EXTS]
    cands += [(p + "/index" if p else "index") + e for e in _NEXT_FILE_EXTS]
    for c in cands:
        if c in files:
            return c
    return None


class _World:
    """The router mount graph over a set of files: resolves a mount operand to its router node (through
    imports and exports: relative specifiers, NodeNext `.js` names of `.ts` files, the tree's tsconfig paths /
    baseUrl and workspace packages (`mods`), and otherwise an alias exactly one module of the tree matches) and
    composes every router's full prefixes. Nodes are (rel, kind, name): kind "var" (a router variable) or "fn"
    (a plugin function). A mount declared in a test file (`is_test`) never shapes a router that a non-test file
    mounts too."""

    def __init__(self, loader, files: set, is_test=None, mods=None):
        self._loader = loader
        self._files = files
        self._is_test = is_test
        self._mods = mods
        self._facts: dict = {}
        self.mounts_into: dict = {}
        self._memo: dict = {}
        self._by_suffix = None          # module path suffix -> the files whose module path ends with it

    @classmethod
    def single(cls, f: _File, rf: _RouterFacts):
        w = cls(lambda rel: rf if rel == f.rel else None, {f.rel})
        w._facts[f.rel] = rf
        w.add_mounts(f.rel)
        return w

    def facts(self, rel):
        if rel not in self._facts:
            try:
                self._facts[rel] = self._loader(rel)
            except Exception:  # noqa: BLE001
                self._facts[rel] = None
        return self._facts[rel]

    def _resolve_spec(self, rel: str, spec: str):
        if self._mods is not None:
            got = self._mods.resolve(rel, spec)
            if got[0] == "file":
                return got[1]
            if got[0] != "guess":
                return None
        elif spec.startswith("."):
            return _file_for(self._files, posixpath.join(posixpath.dirname(rel), spec))
        return self._resolve_alias(rel, spec)

    def _resolve_alias(self, rel: str, spec: str):
        """A non-relative specifier: the one file of the tree whose module path module_scope's rule matches
        (an alias `@/routes/users` -> the module path ending with routes/users), else None — no match, or
        several (never a guess between them)."""
        kind, _, key = module_scope(rel, spec).partition(":")
        if kind not in ("module", "modsuffix") or not key:
            return None
        if self._by_suffix is None:
            idx: dict = {}
            for r in sorted(self._files):
                segs = _module_key(r).split("/")
                for i in range(len(segs)):
                    idx.setdefault("/".join(segs[i:]), []).append(r)
            self._by_suffix = idx
        hits = self._by_suffix.get(key, ())
        if kind == "module":
            hits = [r for r in hits if _module_key(r) == key]
        return hits[0] if len(hits) == 1 else None

    def node_of(self, rel: str, ref, depth: int = 0):
        """A mount operand in file `rel` -> its router node, or None (unknown / not a router)."""
        if ref is None or depth > _MAX_IMPORT_HOPS:
            return None
        rf = self.facts(rel)
        if rf is None:
            return None
        if ref[0] == "local":
            name = ref[1]
            if name in rf.routers:
                return (rel, "var", name)
            if name in rf.fns:
                return (rel, "fn", name)
            imp = rf.f.imports.get(name)
            if imp is not None:
                return self.node_of(rel, ("import",) + imp, depth + 1)
            return None
        if ref[0] == "import":
            target = self._resolve_spec(rel, ref[1])
            if target is None:
                return None
            tf = self.facts(target)
            if tf is None:
                return None
            exported = ref[2]
            if exported in ("default", "="):
                local = tf.f.exports.get("=" if exported == "=" else "default") or tf.f.exports.get("default")
            elif exported == "*":
                return None
            else:
                local = tf.f.exports.get(exported)
            if local:
                return self.node_of(target, ("local", local), depth + 1)
            return self._reexported(target, exported, depth)
        return None

    def _reexported(self, target: str, exported: str, depth: int):
        """The router node a module re-exports (`export { default as Users } from './user.routes'`, `export *
        from './orders'`), followed to the module that defines it (_Modules.lookup), or None."""
        if self._mods is None:
            return None
        dflt = exported in ("default", "=")
        got = self._mods.lookup(target, None if dflt else exported, default=dflt)
        if got[0] == "def":
            return self.node_of(got[1], ("local", got[2]), depth + 1)
        if got[0] == "anon":                             # an anonymous default plugin function ("<default>")
            rf = self.facts(got[1])
            local = rf.f.exports.get("default") if rf is not None else None
            return self.node_of(got[1], ("local", local), depth + 1) if local else None
        return None

    def add_mounts(self, rel: str) -> None:
        rf = self.facts(rel)
        test = bool(rf is not None and rf.mounts and self._is_test is not None and self._is_test(rel))
        for parent, child_ref, prefix, fw in (rf.mounts if rf is not None else ()):
            child = self.node_of(rel, child_ref)
            if child is None:
                continue
            pnode = (rel,) + parent if parent[0] == "var" and parent[1] in rf.routers else \
                ((rel,) + parent if parent[0] == "fn" else None)
            if pnode == child:
                continue
            self.mounts_into.setdefault(child, []).append((pnode, prefix, test))
        self._memo.clear()

    def own_prefix(self, node):
        rf = self.facts(node[0])
        if rf is None or node[1] != "var":
            return ""
        got = rf.routers.get(node[2])
        return (got[1] or "") if got else ""

    def full(self, node, visiting=frozenset()) -> tuple:
        """(prefixes, clean): every full prefix of `node` as (text | None, unresolved tuple)."""
        if node in self._memo:
            return self._memo[node], True
        own = self.own_prefix(node)
        out: list = []
        clean = True
        if len(visiting) < _MAX_MOUNT_DEPTH:
            mounts = self.mounts_into.get(node, ())
            if any(not test for _p, _x, test in mounts):  # a test's mount (`app.use(router)` in a spec)
                mounts = [m for m in mounts if not m[2]]   # never shapes a router production mounts
            for parent, prefix, _test in mounts:
                if parent is not None and (parent in visiting or parent == node):
                    clean = False
                    continue
                pres, c = ([(None, ())], True) if parent is None else self.full(parent, visiting | {node})
                clean = clean and c
                for ptext, punres in pres:
                    if prefix is None:
                        out.append((_concat_path(ptext or "", own), punres + ("<dynamic mount prefix>",)))
                    else:
                        out.append((_concat_path(_concat_path(ptext or "", prefix), own), punres))
        else:
            clean = False
        if not out:
            out = [(own, ())]
        out = list(dict.fromkeys(out))[:_MAX_PATHS]
        if clean:
            self._memo[node] = out
        return out, clean

    def route_paths(self, rel: str, owner, raw_paths) -> list:
        node = (rel,) + owner
        pres, _clean = self.full(node)
        out = []
        for ptext, unres in pres:
            for raw in raw_paths:
                out.append((_concat_path(ptext or "", raw) if raw else (ptext or "/"), unres))
        return out


# ---- the tree's modules (finalize) ----------------------------------------------------------------------
_CONFIG_NAMES = ("tsconfig.json", "jsconfig.json")
_EXPORT_FROM = re.compile(r"export(?<![\w$.]export)\s+(?:type\s+)?(?:\*\s*(?:as\s+([A-Za-z_$][\w$]*)\s*)?|"
                          r"\{([^{}]{0,4000})\}\s*)from\s*(['\"])")
_TOP_DECL = re.compile(r"(class|interface|enum|function|const|let|var|type|namespace)\s*\*?\s+([A-Za-z_$][\w$]*)")
_DECL_WORDS = frozenset({"class", "interface", "enum", "function", "const", "let", "var", "type", "namespace"})
_TYPE_DECLS = frozenset({"class", "interface", "enum", "type"})
_TYPE_ALIAS = re.compile(r"\s*(?:<[^=;{}]{0,300}>\s*)?=(?![=>])")
_JSON_STR = re.compile(r'"(?:[^"\\\n]|\\.)*"?')
_JSON_TRAILING = re.compile(r',(\s*[}\]])|("(?:[^"\\]|\\.)*")')


def _jsonc(text: str):
    """A tsconfig-style JSON object: // and /* */ comments and trailing commas allowed. None when unreadable."""
    out: list = []
    i, n = 0, len(text)
    while i < n:                                         # comments out (strings kept)
        c = text[i]
        if c == '"':
            m = _JSON_STR.match(text, i)
            out.append(m.group(0))
            i = m.end()
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            j = i + 1
            while j < n and text[j] not in '"/':
                j += 1
            out.append(text[i:j])
            i = j
    try:
        v = json.loads(_JSON_TRAILING.sub(lambda m: m.group(2) or m.group(1), "".join(out)))
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def _paths_targets(paths, spec: str) -> list:
    """The targets of the tsconfig `paths` pattern that matches `spec` with its `*` filled in: an exact pattern
    first, else the `*` pattern with the longest prefix (TypeScript's rule); [] when none matches."""
    if not isinstance(paths, dict):
        return []
    best = None
    for pat, targets in paths.items():
        if not isinstance(pat, str) or not isinstance(targets, list):
            continue
        if "*" not in pat:
            if pat == spec:
                return [t for t in targets if isinstance(t, str)]
            continue
        pre, _, suf = pat.partition("*")
        if spec.startswith(pre) and spec.endswith(suf) and len(spec) >= len(pre) + len(suf) \
                and (best is None or len(pre) > len(best[0])):
            best = (pre, targets, spec[len(pre):len(spec) - len(suf)])
    return [t.replace("*", best[2], 1) for t in best[1] if isinstance(t, str)] if best else []


_CONDITIONS = frozenset({"types", "node", "import", "require", "default"})


def _export_targets(v, depth: int = 0) -> list:
    """The files a package.json `exports` / `imports` entry names, in Node's order: a conditions object's keys
    in their own order (the conditions a Node / TypeScript build matches: types, node, import, require,
    default), an array's entries in turn. The caller takes the first the tree holds (a `./dist/...` target
    of an unbuilt tree gives way to the next)."""
    if isinstance(v, str):
        return [v]
    out: list = []
    if depth < 4 and isinstance(v, dict):
        for k, x in v.items():
            if k in _CONDITIONS:
                out += _export_targets(x, depth + 1)
    elif depth < 4 and isinstance(v, list):
        for x in v:
            out += _export_targets(x, depth + 1)
    return out


def _imports_targets(imports, spec: str) -> list:
    """The targets a package.json `imports` map gives the subpath import `spec` (`#src/users` under
    "#src/*": "./src/*"): an exact key first, else the `*` pattern with the longest prefix (Node's rule), a
    conditions object or a list read like `exports` (_export_targets). [] when no key matches."""
    if not isinstance(imports, dict):
        return []
    if spec in imports:
        return _export_targets(imports[spec])
    best = None
    for key, value in imports.items():
        if not isinstance(key, str) or key.count("*") != 1:
            continue
        pre, _, suf = key.partition("*")
        if spec.startswith(pre) and spec.endswith(suf) and len(spec) >= len(pre) + len(suf) \
                and (best is None or len(pre) > len(best[0])):
            best = (pre, value, spec[len(pre):len(spec) - len(suf)])
    return [t.replace("*", best[2]) for t in _export_targets(best[1])] if best else []


class _ModFacts:
    """What one module exports, for finalize's lookups (_Modules.lookup)."""
    __slots__ = ("imports", "exports", "decls", "named", "stars", "nss", "anon_default", "code", "prop_defs", "clash",
                 "cjs_keys", "dshape", "vshapes", "heritage")

    def __init__(self, f: _File):
        code = f.code
        self.code = code
        self.imports = f.imports
        self.exports = {k: v for k, v in f.exports.items() if not v.startswith("<")}
        self.prop_defs = {k: f.line(p) for k, p in f.prop_defs.items()}   # exports.x = <expr> -> its line
        self.decls: dict = {}                            # top-level declaration name -> (keyword, line)
        pos = 0
        while True:
            m = _TOP_DECL.search(code, pos)
            if m is None:
                break
            s, name = m.start(), m.group(2)
            if name in _DECL_WORDS:                      # `const enum X`: read again from the second keyword
                pos = m.start(2)
                continue
            pos = m.end()
            kw = m.group(1)
            if (s and (code[s - 1].isalnum() or code[s - 1] in "_$.")) or f.depth(s) != 0:
                continue
            if kw == "type" and not _TYPE_ALIAS.match(code, m.end()):
                continue
            had = self.decls.get(name)
            # a name declared both as a type and as a value (`interface X` + `const X`, zod): a type
            # reference means the type, so its line is the one kept
            if had is None or (had[0] not in _TYPE_DECLS and kw in _TYPE_DECLS):
                self.decls[name] = (kw, f.line(s))
        self.named: dict = {}                            # re-exported name -> (specifier, its name there)
        self.stars: list = []                            # `export * from` specifiers
        self.nss: dict = {}                              # `export * as ns from`: ns -> specifier
        for m in (_EXPORT_FROM.finditer(code) if "export" in code else ()):
            spec = f.lit(m.start(3))
            if spec is None:
                continue
            if m.group(2) is not None:
                for item in m.group(2).split(","):
                    toks = item.replace("type ", " ").split()
                    if len(toks) >= 3 and toks[1] == "as":
                        self.named.setdefault(toks[2], (spec, toks[0]))
                    elif len(toks) == 1 and _IDENT.fullmatch(toks[0]):
                        self.named.setdefault(toks[0], (spec, toks[0]))
            elif m.group(1):
                self.nss.setdefault(m.group(1), spec)
            else:
                self.stars.append(spec)
        # `exports.x = catchAsync(name)`: the module's own `name`, wrapped once (else the property's own definition)
        for k, (name, off) in f.wrapped.items():
            if k not in self.exports and k not in self.prop_defs:
                if name in self.decls or name in self.imports:
                    self.exports[k] = name
                else:
                    self.prop_defs[k] = f.line(off)
        dm, cm = _EXPORT_DEFAULT.search(code), _MODULE_EXPORTS.search(code)
        self.anon_default = "default" not in self.exports and "default" not in self.named and (
            dm is not None or cm is not None)
        # What an object the module exports holds, for a member read on it (_object_member): the anonymous
        # default's shape and each exported top-level const / let / var's, plus the CommonJS keys set by name.
        self.cjs_keys = f.cjs_keys
        x = dm if dm is not None else cm
        self.dshape = self._shape(f, _value_shape(f, x.end() if x is dm else _cjs_value_start(code, x.end()))) \
            if self.anon_default else None
        # each top-level class with a base: the members it holds itself and its first `extends` (_inherited)
        heirs = [(n, c) for n, c in f.classes.items() if c.top and c.extends and not c.iface and not c.is_expr]
        static = _static_writes(f) if heirs else {}
        self.heritage = {n: (frozenset(_own_names(f, c, static)), c.extends[0][1]) for n, c in heirs}
        # the exported consts' shapes, then those of the local consts a `new X()` or an `extends` names (a class
        # expression read for its members)
        self.vshapes: dict = {}
        want = {n for n in self.exports.values() if self._var(n)} | self._refs([self.dshape])
        want |= {b for _m, b in self.heritage.values() if self._var(b)}
        done: set = set()
        rounds = 0
        while want - done and rounds <= _MAX_HERITAGE:
            rounds += 1
            todo = want - done
            done |= todo
            got = {n: self._shape(f, sh) for n, sh in _var_shapes(f, todo).items()}
            self.vshapes.update(got)
            want |= self._refs(got.values())
        # names defined at two lines (`exports.x = <expr>` or an object's `x() {}` beside a declaration of x): only a
        # node at the line of the one meant is it
        own: dict = {}
        for k, (_kw, ln) in self.decls.items():
            own.setdefault(k, set()).add(ln)
        for k, ln in self.prop_defs.items():
            own.setdefault(k, set()).add(ln)
        for sh in [self.dshape] + list(self.vshapes.values()):
            for k, mem in (sh[1].items() if sh is not None and sh[0] in ("obj", "cls") else ()):
                if mem[0] == "def":
                    own.setdefault(k, set()).add(mem[1])
        self.clash = {k for k, lines in own.items() if len(lines) > 1}

    def _var(self, name) -> bool:
        """`name` is a top-level const / let / var of this module (not an import)."""
        return self.decls.get(name, ("",))[0] in ("const", "let", "var") and name not in self.imports

    def _refs(self, shapes) -> set:
        """The local consts these shapes name as a class: a `new X()`'s X, a class expression's base."""
        out: set = set()
        for sh in shapes:
            ref = sh[1] if sh is not None and sh[0] == "new" else (sh[2] if sh is not None and sh[0] == "cls" else None)
            if ref and self._var(ref):
                out.add(ref)
        return out

    def _shape(self, f: _File, sh):
        """A shape with its object members placed: a wrapped name the module declares or imports is that name, any
        other member a definition at its key's line ("def", line)."""
        if sh is None or sh[0] not in ("obj", "cls"):
            return sh
        out: dict = {}
        for k, mem in sh[1].items():
            if mem[0] == "wrap":
                mem = ("id", mem[1]) if (mem[1] in self.decls or mem[1] in self.imports) else ("def", mem[2])
            out[k] = mem if mem[0] == "id" else ("def", f.line(mem[1]))
        return (sh[0], out) + sh[2:]


class _Modules:
    """Module resolution over the tree for finalize, the way TypeScript and Node resolve with what the tree
    holds: a relative specifier (extensions, index files, a NodeNext `.js` name); the `paths` and `baseUrl` of
    the nearest tsconfig.json / jsconfig.json above the importing file (its relative `extends` chain applied);
    a Node.js subpath import (`#src/x`) through the `imports` of the nearest package.json; a workspace package
    by its package.json `name` (its `exports` / `types` / `main`, else src/index); an alias no config maps
    stays module_scope's guess; anything else is a package the tree does not hold. lookup() says
    where a module's export is defined, following re-exports and the default export. Files are read on demand,
    once each; never raises out (finalize keeps the per-file edges on any failure)."""

    def __init__(self, root: Path, files, loader):
        self.root = root
        self.files = files                               # the TS / JS files the walk kept (root-relative)
        self._loader = loader                            # rel -> _File | None
        self._facts: dict = {}
        self._cfg: dict = {}                             # directory -> (paths base, paths, baseUrl) | None
        self._raw: dict = {}                             # config file -> (baseUrl, paths, paths' directory)
        self._json: dict = {}
        self._isfile: dict = {}
        self._pkgs = None                                # workspace package name -> its directory
        self._scopes: dict = {}                          # directory -> its nearest package.json (_package_scope)
        self._found: dict = {}                           # (module, name, default) -> what lookup() found
        self._busy: set = set()                          # lookups in progress (an `export *` cycle)

    # -- files and JSON --------------------------------------------------------------------------------
    def _is_file(self, rel: str) -> bool:
        if rel not in self._isfile:
            try:
                self._isfile[rel] = (self.root / rel).is_file()
            except OSError:
                self._isfile[rel] = False
        return self._isfile[rel]

    def _read_json(self, rel: str):
        if rel not in self._json:
            text = _read(self.root / rel) if self._is_file(rel) else None
            self._json[rel] = _jsonc(text) if text else None
        return self._json[rel]

    def module_file(self, path: str):
        return _file_for(self.files, path)

    def facts(self, rel: str):
        if rel not in self._facts:
            f = self._loader(rel) if rel in self.files else None
            self._facts[rel] = _ModFacts(f) if f is not None else None
        return self._facts[rel]

    # -- tsconfig / jsconfig ---------------------------------------------------------------------------
    def _raw_config(self, rel: str, depth: int = 0) -> tuple:
        """(baseUrl directory | None, paths | None, the directory of the config that set paths | None) of the
        config file `rel`, the configs it `extends` (relative ones) applied first."""
        if rel in self._raw:
            return self._raw[rel]
        self._raw[rel] = (None, None, None)              # a cycle reads as an empty config
        data = self._read_json(rel) or {}
        d = posixpath.dirname(rel)
        base_url = paths = paths_dir = None
        ext = data.get("extends")
        for e in ([ext] if isinstance(ext, str) else ext if isinstance(ext, list) else []):
            if depth >= 5 or not isinstance(e, str) or not e.startswith("."):
                continue
            p = posixpath.normpath(posixpath.join(d, e))
            if not p.endswith(".json") and not self._is_file(p):
                p += ".json"
            if p == ".." or p.startswith("../") or not self._is_file(p):
                continue
            pb, pp, pd = self._raw_config(p, depth + 1)
            base_url = pb if pb is not None else base_url
            if pp is not None:
                paths, paths_dir = pp, pd
        opts = data.get("compilerOptions")
        if isinstance(opts, dict):
            bu = opts.get("baseUrl")
            if isinstance(bu, str):
                b = posixpath.normpath(posixpath.join(d, bu))
                base_url = None if (b == ".." or b.startswith("../")) else ("" if b == "." else b)
            if isinstance(opts.get("paths"), dict):
                paths, paths_dir = opts["paths"], d
        got = (base_url, paths, paths_dir)
        self._raw[rel] = got
        return got

    def _config(self, d: str):
        """(paths base, paths, baseUrl | None) of the nearest tsconfig.json / jsconfig.json at or above the
        directory `d` (TypeScript's paths resolve against baseUrl, else the config that set them), or None."""
        if d in self._cfg:
            return self._cfg[d]
        got = None
        for name in _CONFIG_NAMES:
            rel = posixpath.join(d, name) if d else name
            if self._is_file(rel):
                base_url, paths, paths_dir = self._raw_config(rel)
                got = (base_url if base_url is not None else (paths_dir or ""), paths or {}, base_url)
                break
        else:
            got = self._config(posixpath.dirname(d)) if d else None
        self._cfg[d] = got
        return got

    # -- workspace packages ------------------------------------------------------------------------------
    def _packages(self) -> dict:
        if self._pkgs is None:
            dirs: set = set()
            for rel in self.files:
                d = posixpath.dirname(rel)
                while d not in dirs:
                    dirs.add(d)
                    if not d:
                        break
                    d = posixpath.dirname(d)
            pk: dict = {}
            for d in sorted(dirs):
                data = self._read_json(posixpath.join(d, "package.json") if d else "package.json")
                name = data.get("name") if data else None
                if isinstance(name, str) and name and name not in pk:
                    pk[name] = d
            self._pkgs = pk
        return self._pkgs

    def _package_file(self, d: str, sub: str):
        data = self._read_json(posixpath.join(d, "package.json") if d else "package.json") or {}
        ex = data.get("exports")
        cands = []
        if isinstance(ex, dict) and any(isinstance(k, str) and k.startswith(".") for k in ex):
            cands += _export_targets(ex.get("./" + sub if sub else "."))
        elif not sub:
            cands += _export_targets(ex)
        if sub:
            cands += [sub, "src/" + sub]
        else:
            cands += [data.get(k) for k in ("types", "typings", "source", "module", "main")] + ["src/index", "index"]
        for c in cands:
            if isinstance(c, str) and c:
                hit = _file_for(self.files, posixpath.join(d, c))
                if hit:
                    return hit
        return None

    def _package_scope(self, d: str):
        """(directory, package.json data) of the nearest package.json at or above the directory `d` (Node's
        package scope), or None."""
        if d not in self._scopes:
            rel = posixpath.join(d, "package.json") if d else "package.json"
            if self._is_file(rel):
                self._scopes[d] = (d, self._read_json(rel) or {})
            else:
                self._scopes[d] = self._package_scope(posixpath.dirname(d)) if d else None
        return self._scopes[d]

    def _subpath_import(self, rel: str, spec: str):
        """Where a Node.js subpath import (`#src/users`) leads, as resolve() says it: the first target the
        `imports` map of the importing file's package scope gives it (_imports_targets) that the tree holds — a
        file of the package, or a package (Node lets `imports` name one). None when no target is there."""
        scope = self._package_scope(posixpath.dirname(rel))
        for target in (_imports_targets(scope[1].get("imports"), spec) if scope else ()):
            if not target or target.startswith("#"):
                continue
            if target.startswith("./"):
                hit = _file_for(self.files, posixpath.join(scope[0], target))
                if hit:
                    return ("file", hit)
            elif not target.startswith((".", "/")):      # Node accepts only "./" targets inside the package
                return self.resolve(rel, target)
        return None

    # -- resolution --------------------------------------------------------------------------------------
    def resolve(self, rel: str, spec: str) -> tuple:
        """Where the specifier `spec` imported by file `rel` leads: ("file", rel2) | ("external",) (a package
        the tree does not hold) | ("guess", "modsuffix:<path>") (an alias no config maps) | ("unknown",)."""
        s = str(spec or "").split("?", 1)[0]
        if not s or s.startswith("/"):
            return ("unknown",)
        if s.startswith("."):
            hit = _file_for(self.files, posixpath.join(posixpath.dirname(rel), s))
            return ("file", hit) if hit else ("unknown",)
        if is_package(s):
            return ("external",)
        cfg = self._config(posixpath.dirname(rel))
        if cfg is not None:
            base, paths, _base_url = cfg
            for target in _paths_targets(paths, s):
                hit = _file_for(self.files, posixpath.join(base, target) if base else target)
                if hit:
                    return ("file", hit)
        if s.startswith("#"):                            # a Node.js subpath import: its package's `imports`
            got = self._subpath_import(rel, s)
            if got is not None:
                return got
        if cfg is not None and cfg[2] is not None:
            base_url = cfg[2]
            hit = _file_for(self.files, posixpath.join(base_url, s) if base_url else s)
            if hit:
                return ("file", hit)
        parts = s.split("/")
        k = 2 if s.startswith("@") else 1
        d = self._packages().get("/".join(parts[:k]))
        if d is not None:
            hit = self._package_file(d, "/".join(parts[k:]))
            return ("file", hit) if hit else ("unknown",)
        ms = module_scope(rel, s)
        return ("guess", ms) if ms.startswith("modsuffix:") else ("external",)

    def lookup(self, rel: str, name, default: bool = False, depth: int = 0) -> tuple:
        """Where module `rel`'s export `name` (its default export when `default`) is defined: ("def", rel2,
        name2, keyword, line) | ("ns", rel2) (a namespace object) | ("ext",) (a package's) | ("anon", rel2) (an
        anonymous default of the module rel2: `export default new X()`, `export default { list }`, a function
        or class with no name) | ("absent",) (the module does not export it) | ("unknown",). Memoized, so a
        lattice of `export *` barrels costs one look per module and name; a re-export cycle reads as unknown."""
        key = (rel, name, default)
        got = self._found.get(key)
        if got is not None:
            return got
        if key in self._busy or depth > _MAX_LOOKUP_DEPTH:
            return ("unknown",)
        self._busy.add(key)
        try:
            got = self._lookup(rel, name, default, depth)
        finally:
            self._busy.discard(key)
        self._found[key] = got
        return got

    def _lookup(self, rel: str, name, default: bool, depth: int) -> tuple:
        mf = self.facts(rel)
        if mf is None:
            return ("unknown",)
        if default:
            if "default" in mf.named:
                return self._follow(rel, *mf.named["default"], depth)
            local = mf.exports.get("default")
            if local is None:
                return ("anon", rel) if mf.anon_default else ("unknown",)
            return self._local(rel, mf, local, depth)
        if name in mf.named:
            return self._follow(rel, *mf.named[name], depth)
        if name in mf.nss:
            return self._follow(rel, mf.nss[name], "*", depth)
        local = mf.exports.get(name)
        if local is not None:
            return self._local(rel, mf, local, depth)
        if name in mf.prop_defs:                         # exports.x = <expr>: the property's own definition
            return ("def", rel, name, "exports", mf.prop_defs[name])
        if name in mf.decls:                             # declared, exported in a way not read (CommonJS)
            return ("def", rel, name) + mf.decls[name]
        found, ext, unknown = [], False, False
        for spec in mf.stars:
            r = self.resolve(rel, spec)
            got = self.lookup(r[1], name, False, depth + 1) if r[0] == "file" else \
                (("ext",) if r[0] == "external" else ("unknown",))
            if got[0] in ("def", "ns", "anon"):          # anon: a re-exported default (`export { default as x }`)
                found.append(got)
            elif got[0] == "ext":
                ext = True
            elif got[0] == "unknown":
                unknown = True
        if len(found) == 1:
            return found[0]
        if found or unknown:
            return ("unknown",)
        if ext:
            return ("ext",)                              # `export * from 'typeorm'` gives it
        return ("unknown",) if re.search(r"(?<![\w$])" + re.escape(name) + r"(?![\w$])", mf.code) else ("absent",)

    def _local(self, rel: str, mf: _ModFacts, local: str, depth: int) -> tuple:
        if local in mf.decls:
            return ("def", rel, local) + mf.decls[local]
        imp = mf.imports.get(local)
        if imp is not None:
            return self._follow(rel, imp[0], imp[1], depth)
        return ("unknown",)

    def _follow(self, rel: str, spec: str, imported: str, depth: int) -> tuple:
        r = self.resolve(rel, spec)
        if r[0] == "external":
            return ("ext",)
        if r[0] != "file":
            return ("unknown",)
        if imported == "*":
            return ("ns", r[1])
        if imported in ("default", "="):
            return self.lookup(r[1], None, True, depth + 1)
        return self.lookup(r[1], imported, False, depth + 1)


_LINE_HINT = re.compile(r"L(\d+),(\d+)\|")


def _rescope(e, mods: _Modules):
    """An edge whose scope names an import, placed in the tree (_place), with the definition's line in front
    ("L<src line>,<dst line>|": a heritage edge's own hint keeps its src line, any other edge gets src 0), so
    edge_resolve picks the declaration among same-named nodes of that file. None: the edge is dropped."""
    m = _LINE_HINT.match(e.scope or "")
    if m is None:
        got, line = _place(e, mods)
        return got._replace(scope=f"L0,{line}|{got.scope}") if got is not None and line else got
    inner = e._replace(scope=e.scope[m.end():])
    got, line = _place(inner, mods)
    if got is None or (got is inner and not line):
        return None if got is None else e
    dst = m.group(2) if m.group(2) != "0" or not line else str(line)
    return got._replace(scope=f"L{m.group(1)},{dst}|{got.scope}")


def _place(e, mods: _Modules) -> tuple:
    """(edge | None, the definition's line or 0): an edge whose scope names an import, placed in the tree: in
    the module that defines the name ("exact:"; renamed to that definition for a default import), a module
    object's member at what the object holds under that name (_object_member: an entry's definition, a class's
    method `X.m`, inherited ones included, with no line; also behind a barrel); dropped (None) when the import names
    a package, an anonymous
    type or handler, or nothing the module or the object exports. When the definition cannot be found the import's
    own module stays preferred ("pref:", edge_resolve's global fallback), and an alias no config maps stays a guess
    ("modsuffix:"). Any other edge is returned unchanged."""
    s = e.scope or ""
    pref = s.startswith("pref:")
    kind, _, rest = (s[5:] if pref else s).partition(":")
    if kind not in ("module", "spec") or not rest:
        return e, 0
    arg, _, note = rest.partition("#")
    if kind == "module":
        hit = mods.module_file(arg)
        res = ("file", hit) if hit else ("unknown",)
    else:
        res = mods.resolve(e.src_file, _scope_spec(arg))
    if res[0] == "external":
        return None, 0
    if res[0] == "guess":
        return e._replace(scope=("pref:" if pref else "") + res[1]), 0
    if res[0] != "file":
        if kind == "module":
            return (e._replace(scope=("pref:" if pref else "") + "module:" + arg) if note else e), 0
        return (e._replace(scope="") if pref else None), 0     # a workspace package whose entry is not in the tree
    fmod = "module:" + _module_key(res[1])
    if pref:
        # The import names the dst, except for a TypeORM onDelete CASCADES, emitted principal first: there the
        # imported principal is the src and the dst is the class the file declares.
        on_src = e.edge_type == "CASCADES" and (e.attrs or {}).get("strategy") in ("CASCADE", "SET NULL")
        cls_name, dot, tail = (e.src_name if on_src else e.dst_name).partition(".")
        if note and note != "default":                   # ns.Base through `import { ns }`
            ns = mods.lookup(res[1], note)
            got = mods.lookup(ns[1], cls_name) if ns[0] == "ns" else ("unknown",)
        else:
            got = mods.lookup(res[1], cls_name, default=note == "default")
        if got[0] == "def":                              # (the engine node-ifies a type alias as a :Class too)
            up = _inherited(mods, got, tail) if tail and got[3] == "class" and e.edge_type == "ROUTES_TO" else None
            if up is not None and up[0] in ("method", "def"):
                sc = "exact:module:" + _module_key(up[1])
                return e._replace(dst_name=up[2], scope=sc), (_def_line(mods, up) if up[0] == "def" else 0)
            name, scope = got[2] + dot + tail, "exact:module:" + _module_key(got[1])
            if on_src:
                return e._replace(src_name=name, scope=scope), 0
            return e._replace(dst_name=name, scope=scope), (_def_line(mods, got) if not tail else 0)
        if got[0] != "unknown":
            return None, 0
        return e._replace(scope="pref:" + fmod), 0
    member = e.dst_name.rsplit(".", 1)[-1]
    if note == "*":
        got = mods.lookup(res[1], member)
        if got[0] == "def":                              # a namespace's member IS that definition: placed like a
            return e._replace(dst_name=got[2], scope="exact:module:" + _module_key(got[1])), _def_line(mods, got)
        if got[0] == "ns":                               # a namespace object
            return e._replace(scope="module:" + _module_key(got[1])), 0
        return (e._replace(scope=fmod) if got[0] == "unknown" else None), 0
    if note:                                             # the object a named import binds (`import { users }`)
        obj = mods.lookup(res[1], note)
        if obj[0] == "ns":
            return e._replace(scope="module:" + _module_key(obj[1])), 0
        if obj[0] not in ("def", "anon"):
            return (e._replace(scope=fmod) if obj[0] == "unknown" else None), 0
        home, got = _object_member(mods, obj, member)
    else:                                                # what a default / `=` import or require() binds
        home, got = _object_member(mods, mods.lookup(res[1], None, default=True), member, res[1])
    if got is None:                                      # a shape not read: the name inside the object's module
        hmod = "module:" + _module_key(home)
        return (e if hmod == s else e._replace(scope=hmod)), 0
    if got[0] == "none":
        return None, 0
    scope = "exact:module:" + _module_key(got[1])
    if got[0] == "method":
        return e._replace(dst_name=got[2], scope=scope), 0
    return e._replace(dst_name=got[2], scope=scope), _def_line(mods, got)


def _def_line(mods: _Modules, got: tuple) -> str:
    """The line hint of a definition lookup() found ("def", module, name, keyword, line): "<line>", or "<line>!"
    when that module defines the name at two lines (an `exports.x = <expr>` or an object's `x() {}` beside a
    declaration of x): then only a node at this line is the definition meant (edge_resolve drops the edge when
    none is there)."""
    mf = mods.facts(got[1])
    return f"{got[4]}!" if mf is not None and got[2] in mf.clash else str(got[4])


def _object_member(mods: _Modules, obj: tuple, member: str, rel: str = None) -> tuple:
    """(home module, what `member` is) on an object an import binds: `obj` is what lookup() found for it (a default /
    `=` import's: the module's default export, `rel` that module, whose own exports are the object when it has
    none). Home is the module that holds the object. What the member is: the definition the object holds under
    that name ("def", ...: an `exports.x = ...`, an object literal's entry — `module.exports = { show }`, `export
    default { login: renderLogin }`, a const's `{ ... }` or `controller.x = ...` —, a class expression's method at
    its line); a declared class's method ("method", module, "X.m") on that class or an instance (`new X()`, X
    declared or imported, a constructor function too), the class in the `extends` chain that declares it
    (_inherited); ("none",) when the object holds no such member (another key, a function); None when its shape is
    not read (the name inside home, edge_resolve's module rule). Never a private function of the member's name."""
    kind = obj[0]
    if kind == "ns":                                     # a namespace held as the default (`export default ns`)
        got = mods.lookup(obj[1], member)
        if got[0] == "def":
            return obj[1], got
        return (got[1], None) if got[0] == "ns" else (obj[1], None if got[0] == "unknown" else ("none",))
    if kind not in ("def", "anon"):
        if kind == "unknown" and rel is not None:        # no default: the module's own exports are the object
            got = mods.lookup(rel, member)
            return rel, (got if got[0] == "def" else None)
        return rel or "", (None if kind == "unknown" else ("none",))
    home = obj[1]
    mf = mods.facts(home)
    if mf is None:
        return home, None
    if (kind == "anon" or obj[2] in (mf.exports.get("="), mf.exports.get("default"))) and member in mf.cjs_keys:
        got = mods.lookup(home, member)                  # `module.exports.x = ...` / `exports.x = ...`
        return home, (got if got[0] == "def" else ("none",))
    if kind == "def" and obj[3] == "class":              # the class itself: its static method
        return home, _inherited(mods, obj, member)
    shape = mf.dshape if kind == "anon" else (mf.vshapes.get(obj[2]) if obj[3] in ("const", "let", "var") else None)
    if shape is None:
        return home, None
    if shape[0] == "new":
        return home, _instance_member(mods, home, mf, shape[1], member)
    if shape[0] not in ("obj", "cls"):
        return home, ("none",)                           # a function: no member of it is a definition
    return home, _shape_member(mods, home, mf, shape, member)


def _shape_member(mods: _Modules, home: str, mf: _ModFacts, shape: tuple, member: str, depth: int = 0) -> tuple:
    """`member` of an object literal or class expression module home makes (`shape`): its entry (a name the module
    declares or imports, else a definition at its line), for a class expression else what its `extends` chain gives
    (_inherited); ("none",) when it holds no such member."""
    mem = shape[1].get(member)
    if mem is None:
        if shape[0] == "cls" and shape[2] and depth < _MAX_HERITAGE:
            up = _inherited(mods, _class_ref(mods, home, mf, shape[2]), member, depth + 1)
            if up is not None:
                return up
        return ("none",)
    if mem[0] == "def":
        return ("def", home, member, "exports", mem[1])
    got = mods._local(home, mf, mem[1], 0)
    return got if got[0] == "def" else ("none",)


def _instance_member(mods: _Modules, home: str, mf: _ModFacts, ctor: str, member: str) -> tuple:
    """`member` of `new <ctor>()` made in module home: what the class (or constructor function) ctor names there —
    declared, imported, `ns.X` on a namespace import, a class expression — gives it (_inherited), else ("none",)."""
    got = _inherited(mods, _class_ref(mods, home, mf, ctor), member)
    return got if got is not None else ("none",)


def _class_ref(mods: _Modules, home: str, mf: _ModFacts, ref: str) -> tuple:
    """What a class reference (`X`, `ns.X`) in module home names, as lookup() says it: its import followed (`const X =
    require('./x')` is X's import, not its value), else its declaration there."""
    parts = ref.split(".")
    if len(parts) > 2:
        return ("unknown",)
    imp = mf.imports.get(parts[0])
    got = mods._follow(home, imp[0], imp[1], 0) if imp is not None else mods._local(home, mf, parts[0], 0)
    if len(parts) == 2:
        got = mods.lookup(got[1], parts[1]) if got[0] == "ns" else ("unknown",)
    return got


def _inherited(mods: _Modules, got: tuple, member: str, depth: int = 0):
    """`member` of the class lookup() found as `got`, read the way JavaScript looks it up: the class's own method, else
    its base's (the first `extends`, in its module or through its import), and so on. A declared class gives `X.m`, a
    class expression its bare method at its line (_shape_member), a constructor function `F.m` (its prototype's). A
    declared class whose body lacks the member and whose base the reader cannot follow is the answer itself (`X.m`).
    None when `got` is no class the reader reads."""
    kind = got[0]
    if kind == "def":
        home, name, kw = got[1], got[2], got[3]
        if kw == "function":
            return ("method", home, f"{name}.{member}")
        mf = mods.facts(home)
        if kw == "class":
            info = mf.heritage.get(name) if mf is not None else None
            if info is not None and member not in info[0] and depth < _MAX_HERITAGE:
                up = _inherited(mods, _class_ref(mods, home, mf, info[1]), member, depth + 1)
                if up is not None:
                    return up
            return ("method", home, f"{name}.{member}")
        shape = mf.vshapes.get(name) if mf is not None and kw in ("const", "let", "var") else None
    elif kind == "anon":
        home = got[1]
        mf = mods.facts(home)
        shape = mf.dshape if mf is not None else None
    else:
        return None
    if shape is None or shape[0] != "cls":
        return None
    return _shape_member(mods, home, mf, shape, member, depth)


def _resolve_imports(edges: list, mods: _Modules) -> list:
    """Every edge placed in the tree by _rescope; the ORIGINAL list when nothing changes."""
    out: list = []
    changed = False
    for e in edges:
        ne = _rescope(e, mods)
        changed = changed or ne is not e
        if ne is not None:
            out.append(ne)
    return out if changed else edges


def modules_for(root, files, loader=None) -> _Modules:
    """The tree's module resolver for a consumer outside finalize — build/receiver_facts_ts.py's binder (typed-
    receiver call linking) — so a TS / JS import specifier is placed in the tree by THE rule finalize places
    typed edges with (_Modules.resolve: relative paths with NodeNext `.js` names of `.ts` files and index files,
    the nearest tsconfig / jsconfig `paths` + `baseUrl` with relative `extends`, package.json `imports`,
    workspace packages, an unmapped alias as a "guess", any other bare specifier a package). `files` = the TS / JS
    files of the tree (root-relative, '/'), `loader` rel -> _File | None for lookup() (default: reads nothing —
    resolve() alone needs no file contents). Additive: recognize() / finalize() never call it."""
    return _Modules(Path(root), set(files), loader if loader is not None else (lambda rel: None))


# ---- Next.js file-system routes -------------------------------------------------------------------
_NEXT_EXPORT_FN = re.compile(
    r"(?<![\w$.])export\s+(?:async\s+)?function\s*\*?\s*(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s*[<(]")
_NEXT_EXPORT_CONST = re.compile(
    r"(?<![\w$.])export\s+(?:const|let|var)\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s*(?::[^=]{0,200})?=(?!=)\s*")


def _app_root(parts: list, marker: str):
    """Index of the Next.js `app` / `pages` directory in path `parts`: the last one right after `src`,
    else the first one."""
    idx = [i for i, p in enumerate(parts[:-1]) if p == marker]
    if not idx:
        return None
    after_src = [i for i in idx if i > 0 and parts[i - 1] == "src"]
    return after_src[-1] if after_src else idx[0]


def _next_segment(seg: str):
    """A folder / file name -> its URL segment ('' to drop it); None when the folder is not routable."""
    if seg.startswith("_"):
        return None
    if seg.startswith("@"):
        return ""
    if seg.startswith("(") and seg.endswith(")"):
        return ""
    if seg.startswith("("):                               # intercepting (.)photo / (..)photo
        seg = seg[seg.find(")") + 1:]
    m = re.fullmatch(r"\[\[\.\.\.([^\]]+)\]\]", seg)
    if m:
        return f"*{m.group(1)}?"
    m = re.fullmatch(r"\[\.\.\.([^\]]+)\]", seg)
    if m:
        return f"*{m.group(1)}"
    m = re.fullmatch(r"\[([^\]]+)\]", seg)
    if m:
        return f":{m.group(1)}"
    return seg


def _next_path(segs: list):
    out = []
    for s in segs:
        u = _next_segment(s)
        if u is None:
            return None
        if u:
            out.append(u)
    return "/" + "/".join(out)


def _next_routes(f: _File, em: _Emitter) -> None:
    rel = f.rel
    parts = rel.split("/")
    base = parts[-1]
    low = base.lower()
    if not low.endswith(_NEXT_FILE_EXTS):
        return
    stem = base.rsplit(".", 1)[0]
    code = f.code
    i = _app_root(parts, "app")
    if stem == "route" and i is not None:
        path = _next_path(parts[i + 1:-1])
        if path is None:
            return
        seen = set()
        for m in _NEXT_EXPORT_FN.finditer(code):
            verb = m.group(1)
            if verb in f.funcs and verb not in seen:
                seen.add(verb)
                em.emit(f.stem, verb, "ROUTES_TO", "LOCAL",
                        {"method": verb, "path": path, "framework": "nextjs"})
        for m in _NEXT_EXPORT_CONST.finditer(code):
            verb = m.group(1)
            if verb in seen:
                continue
            if verb in f.funcs:
                seen.add(verb)
                em.emit(f.stem, verb, "ROUTES_TO", "LOCAL",
                        {"method": verb, "path": path, "framework": "nextjs"})
                continue
            t = _callable(f, m.end(), _expr_end(f, m.end(), len(code)), final=True)
            if t:
                seen.add(verb)
                em.emit(f.stem, t[0], "ROUTES_TO", t[1], {"method": verb, "path": path, "framework": "nextjs"},
                        t[2])
        for m in _EXPORT_LIST.finditer(code):
            for item in m.group(1).split(","):
                toks = item.split()
                if len(toks) == 3 and toks[1] == "as" and toks[2] in _NEXT_METHODS and toks[2] not in seen:
                    t = _callable_name(f, toks[0])
                    if t:
                        seen.add(toks[2])
                        em.emit(f.stem, t[0], "ROUTES_TO", t[1],
                                {"method": toks[2], "path": path, "framework": "nextjs"})
        return
    j = _app_root(parts, "pages")
    if j is None or j + 1 >= len(parts) - 1 or parts[j + 1] != "api":
        return
    segs = parts[j + 1:-1] + ([] if stem == "index" else [stem])
    path = _next_path(segs)
    if path is None:
        return
    target = None
    dm = re.search(r"(?<![\w$.])export\s+default\s+(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(", code)
    if dm:
        target = (dm.group(1), "LOCAL", "")
    else:
        dflt = f.exports.get("default")
        if dflt and dflt in f.funcs:
            target = (dflt, "LOCAL", "")
        elif dflt:
            target = _callable_name(f, dflt)
        else:
            em_ = _EXPORT_DEFAULT.search(code) or _MODULE_EXPORTS.search(code)
            if em_:
                t = _callable(f, em_.end(), _expr_end(f, em_.end(), len(code)), final=True)
                if t:
                    target = t
    if target:
        em.emit(f.stem, target[0], "ROUTES_TO", target[1], {"method": "*", "path": path, "framework": "nextjs"},
                target[2])


def _callable_name(f: _File, name: str):
    """A name an export statement (module level) refers to -> (dst, conf hint, scope), or None."""
    if name in f.funcs and f.fn_visible(name, None):
        return name, "LOCAL", ""
    if name in f.imports and not is_package(f.imports[name][0]):
        spec, imported = f.imports[name]
        return ((name if imported in ("default", "*", "=") else imported), "NAME",
                _pref_scope(f, spec, "default" if imported in ("default", "=") else ""))
    return None


# ---- Angular Routes arrays --------------------------------------------------------------------------
_NG_ROUTES_DECL = re.compile(_DECL_KW + r"\s+[A-Za-z_$][\w$]*\s*:\s*(?:Routes|Route\s*\[\s*\])\s*=\s*\[")
_NG_ROUTES_CALL = re.compile(r"\b(?:forRoot|forChild|provideRouter)\s*\(\s*\[")
# Angular's router given a NAME: `RouterModule.forChild(routes)` / `provideRouter(routes)`.
_NG_ROUTES_REF = re.compile(r"(RouterModule\s*\.\s*for(?:Root|Child)|provideRouter)\s*\(\s*([A-Za-z_$][\w$]*)\s*[,)]")


def _angular_routes(f: _File, em: _Emitter) -> None:
    code = f.code
    if "path" not in code or not any(w in code for w in ("Routes", "Route", "forRoot", "forChild", "provideRouter")):
        return
    seen_arrays = set()
    for rx in (_NG_ROUTES_DECL, _NG_ROUTES_CALL):
        for m in rx.finditer(code):
            o = m.end() - 1
            if o in seen_arrays:
                continue
            seen_arrays.add(o)
            _ng_array(f, em, o, "", 0)
    # An unannotated `const routes = [...]` handed to Angular's router (imported from @angular/router) by name.
    for m in _NG_ROUTES_REF.finditer(code):
        root = "provideRouter" if m.group(1) == "provideRouter" else "RouterModule"
        if f.import_spec(root) != "@angular/router":
            continue
        span = _const_span(f, m.group(2), m.start(2))
        v = _value(f, *span) if span else ("other",)
        if v[0] == "arr" and v[1] not in seen_arrays:
            seen_arrays.add(v[1])
            _ng_array(f, em, v[1], "", 0)


def _ng_array(f: _File, em: _Emitter, o: int, prefix: str, depth: int) -> None:
    if depth > 8:
        return
    close = f.close_of(o, len(f.code))
    for s, e in _split(f, o + 1, close):
        v = _value(f, s, e)
        if v[0] != "obj":
            continue
        ents = _entries(f, v[1])
        pv = ents.get("path")
        if not pv:
            continue
        w = _value(f, *pv)
        if w[0] != "str":
            continue
        path = _join_path(prefix, w[1])
        cv = ents.get("component")
        if cv:
            bare, root = _arg_class(f, *cv)
            tgt, conf, scope = _class_target(f, bare, root)
            if tgt:
                em.emit(f.stem, tgt, "ROUTES_TO", conf, {"method": "VIEW", "path": path, "framework": "angular"},
                        scope)
        for hook in ("canActivate", "canActivateChild", "canMatch", "canDeactivate", "canLoad"):
            hv = ents.get(hook)
            if not hv:
                continue
            arr = _value(f, *hv)
            if arr[0] != "arr":
                continue
            for hs, he in _split(f, arr[1] + 1, arr[2]):
                t = _callable(f, hs, he)
                if t is None:
                    bare, root = _arg_class(f, hs, he)
                    tgt, conf, scope = _class_target(f, bare, root)
                    t = (tgt, conf, scope) if tgt else None
                if t:
                    em.emit(f.stem, t[0], "HOOKS", t[1], {"event": "guard", "path": path}, t[2])
        ch = ents.get("children")
        if ch:
            arr = _value(f, *ch)
            if arr[0] == "arr":
                _ng_array(f, em, arr[1], path, depth + 1)


# ---- the registry post-pass -------------------------------------------------------------------------
_ROUTER_FRAMEWORKS = {"express", "fastify", "koa", "hono"}
_MOUNT_WORDS = (".use(", ".register(", ".route(", "setGlobalPrefix(", "NestFactory")


def _merge_routes(edges: list) -> list:
    """One handler routed more than once from one file (PUT + PATCH on the same path) resolves to ONE
    edge (edge_resolve keeps the first row of a (src, dst, type)), so fold the routes into that first
    edge: `method` joined when the paths agree, else `routes` lists every "METHOD path". Only the SAME
    handler folds — the same name in the same module scope with the same hint, so it resolves to the same
    row: `articles.show` and `users.show` (two module objects), or two `ListComponent`s imported under
    aliases, stay two edges. One Angular guard on several routes folds the same way, `paths` listing every
    guarded path (sorted) and `path` the first of them."""
    first: dict = {}
    extra: dict = {}
    for i, e in enumerate(edges):
        if e.edge_type == "ROUTES_TO":
            k = (e.edge_type, e.src_file, e.src_name, e.dst_name, e.scope, e.conf_hint)
        elif e.edge_type == "HOOKS" and "path" in (e.attrs or {}):        # an Angular route guard
            k = (e.edge_type, e.src_file, e.src_name, e.dst_name, e.scope, e.conf_hint, e.attrs.get("event"))
        else:
            continue
        if k in first:
            extra.setdefault(first[k], []).append(i)
        else:
            first[k] = i
    if not extra:
        return edges
    out = list(edges)
    drop = set()
    for i, others in extra.items():
        group = [edges[i]] + [edges[j] for j in others]
        drop.update(others)
        if edges[i].edge_type == "HOOKS":
            paths = sorted({p for e in group for p in ((e.attrs or {}).get("paths") or [(e.attrs or {}).get("path")])
                            if isinstance(p, str)})
            if len(paths) > 1:
                paths = paths[:_MAX_PATHS]
                out[i] = edges[i]._replace(attrs=dict(edges[i].attrs or {}, path=paths[0], paths=paths))
            continue
        pairs = []
        for e in group:
            a = e.attrs or {}
            for p in (a.get("paths") or [a.get("path")]):
                pairs.append((str(a.get("method", "")), p))
        pairs = list(dict.fromkeys(pairs))
        attrs = dict(edges[i].attrs or {})
        paths = {p for _m, p in pairs}
        if len(paths) == 1:
            methods = sorted({m for m, _p in pairs for m in m.split(",") if m})
            attrs["method"] = ",".join(methods)
        else:
            attrs["routes"] = sorted(f"{m} {p}" if p else m for m, p in pairs)[:_MAX_PATHS]
        out[i] = edges[i]._replace(attrs=attrs)
    return [e for k, e in enumerate(out) if k not in drop]


_TSJS_EXTS = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"})


def _iter_paths(root: Path, exclude):
    """(rel, path) for this entry's files with the registry walker's discipline (language_recognizers.walk_files:
    one pruned walk, sorted, case-insensitive suffix, the entry's ignore set matched root-relative) and the
    caller's exclusion rule (a predicate that raises excludes nothing). Used when the registry did not hand
    finalize its walk's file list. Nothing is read here."""
    try:
        import language_recognizers as _lr       # lazy: the registry imports this module
        entry = next((r for r in _lr.REGISTRY if r.recognize is recognize), None)
        listing = _lr.walk_files(root, entry.extensions if entry is not None else _TSJS_EXTS,
                                 entry.ignore_parts if entry is not None else _lr._TS_IGNORE)
    except Exception:  # noqa: BLE001
        return
    for rel, p in listing:
        try:
            if not p.is_file():
                continue
        except OSError:
            continue
        try:
            if exclude is not None and exclude(rel):
                continue
        except Exception:  # noqa: BLE001
            pass
        yield rel, p


def _read(path: Path):
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None


def _test_path_predicate():
    """sync._is_test_path, THE test-code rule (a test directory component or a *.test / *.spec file name),
    imported lazily (sync imports the registry, which imports this module); a mirror of its TypeScript /
    JavaScript half when sync cannot be imported. Never raises."""
    try:
        import sync  # noqa: E402 — the owner of the rule; already loaded on the sync path
        fn = getattr(sync, "_is_test_path", None)
        if callable(fn):
            return fn
    except Exception:  # noqa: BLE001
        pass

    def mirror(rel) -> bool:
        parts = str(rel or "").replace("\\", "/").split("/")
        low = parts[-1].lower()
        return (any(p.lower() in _TEST_DIR_PARTS for p in parts[:-1])
                or any(low.endswith(m + e) for m in (".test", ".spec") for e in _MODULE_EXTS))
    return mirror


def _nearest_dir(d: str, dirs):
    """The nearest of `dirs` at or above the directory `d` ('' is the tree root), else None."""
    while True:
        if d in dirs:
            return d
        up = posixpath.dirname(d)
        if not d or up == d:
            return None
        d = up


def _under(d: str, a: str) -> bool:
    """The directory `d` is `a` or below it ('' is the tree root)."""
    return a == "" or d == a or d.startswith(a + "/")


def _app_roots(boots: set, route_dirs: set, top=None) -> dict:
    """Each NestJS bootstrap directory -> its app's root: the directory itself when a NestJS route lies under
    it, else the nearest directory above it under which routes lie (src/bootstrap/main.ts serving
    src/modules/**) — never one that holds another bootstrap directory, whose app the routes may be, and never
    one above top(b), the bootstrap's own package or, with two or more apps, project (Nest CLI, Nx, an
    angular.json workspace: _project_top)."""
    out: dict = {}
    for b in sorted(boots):
        root = b
        if not any(_under(r, b) for r in route_dirs):
            limit = top(b) if top is not None else None
            a = b
            while a and a != limit:
                a = posixpath.dirname(a)
                if any(o != b and _under(o, a) for o in boots):
                    break
                if any(_under(r, a) for r in route_dirs):
                    root = a
                    break
        out[b] = root
    return out


def _project_top(mods: _Modules, with_projects: bool = True):
    """d -> the directory of d's own package (its nearest package.json) or, when `with_projects`, project (a `root` in
    the tree's nest-cli.json / angular.json / workspace.json `projects`, or an Nx project.json), whichever is nearest;
    None when none is at or above d."""
    roots = set()
    for cfg in (("nest-cli.json", "angular.json", "workspace.json") if with_projects else ()):
        data = mods._read_json(cfg)
        projects = data.get("projects") if isinstance(data, dict) else None
        for p in (projects.values() if isinstance(projects, dict) else ()):
            r = p.get("root") if isinstance(p, dict) else p
            r = posixpath.normpath(r.strip("/")) if isinstance(r, str) and r.strip("/") else "."
            if r != "." and r != ".." and not r.startswith("../"):
                roots.add(r)

    def nx(d: str):
        while not mods._is_file(posixpath.join(d, "project.json") if d else "project.json"):
            if not d:
                return None
            d = posixpath.dirname(d)
        return d

    def top(d: str):
        got = [x for x in ((mods._package_scope(d) or (None,))[0], _nearest_dir(d, roots) if roots else None,
                           nx(d) if with_projects else None) if x is not None]
        return max(got, key=len) if got else None
    return top


def _app_project(mods: _Modules):
    """d -> the root of the APPLICATION project holding directory d (an Nx project.json `projectType`, a
    nest-cli.json `type`, an angular.json / workspace.json `projectType`), else None."""
    roots = set()
    for cfg, key in (("nest-cli.json", "type"), ("angular.json", "projectType"), ("workspace.json", "projectType")):
        data = mods._read_json(cfg)
        projects = data.get("projects") if isinstance(data, dict) else None
        for pj in (projects.values() if isinstance(projects, dict) else ()):
            if isinstance(pj, dict) and pj.get(key) == "application" and isinstance(pj.get("root"), str):
                r = posixpath.normpath(pj["root"].strip("/")) if pj["root"].strip("/") else "."
                if r not in (".", "..") and not r.startswith("../"):
                    roots.add(r)

    def of(d: str):
        e = d
        while True:
            pj = posixpath.join(e, "project.json") if e else "project.json"
            if mods._is_file(pj):
                data = mods._read_json(pj)
                if isinstance(data, dict) and data.get("projectType") == "application":
                    return e
                break
            if not e:
                break
            e = posixpath.dirname(e)
        return _nearest_dir(d, roots) if roots else None
    return of


def _import_scoped(scope) -> bool:
    """The edge's scope names an import finalize places in the tree ("module:" / "spec:", maybe "pref:", after a
    heritage edge's line hint)."""
    s = str(scope or "")
    m = _LINE_HINT.match(s)
    s = s[m.end():] if m else s
    return (s[5:] if s.startswith("pref:") else s).startswith(("module:", "spec:"))


def finalize(workspace_root, edges, exclude=None, files=None) -> list:
    """The registry post-pass (RecognizerEntry.finalize): facts no single file can see.
      * router mounts in another module (`app.use('/users', usersRouter)` with the router imported, a
        Fastify plugin registered with a prefix elsewhere) compose into the routes' paths — recomputed from
        every file's mounts, like framework_edges.compose_route_prefixes; a mount declared in a test file
        never shapes a router that a non-test file mounts too; only ROUTES_TO attrs change;
      * every imported type, guard, handler and module object is placed in the tree (_Modules, _rescope):
        relative imports, the nearest tsconfig.json / jsconfig.json `paths` and `baseUrl`, workspace packages
        by name, barrels and default exports followed. The edge then binds in the module that defines the
        name, or is dropped when the import names a package the tree does not hold, a type alias or nothing;
      * a NestJS `app.setGlobalPrefix('api')` (a literal or a const in scope; no exclude list) prefixes the
        routes of ITS app. With one bootstrap file (`NestFactory.create`, an aliased NestFactory too; not
        createApplicationContext / createMicroservice) the app is its whole package, less the routes in another
        package or in another APPLICATION project (_app_project) whose bootstrap is not seen; with two or more,
        the controllers under the nearest directory holding a bootstrap file (when no route lies under it, as for
        src/bootstrap/main.ts, the nearest directory above it holding routes and no other bootstrap, within its
        package and its project: _app_roots), or under the calling file's
        own directory when no bootstrap file is above it. An app that sets no prefix, an unreadable one or two
        different ones gets none; a test file never sets one; a route on a class with no @Controller gets none;
      * a Next.js route is anchored at its project root, the nearest directory holding a next.config.* or a
        package.json that depends on next: its `app` (else `src/app`) or `pages/api` directory gives the path,
        and a route file elsewhere under that root is no route. With no root found the per-file rule stays;
      * one handler routed more than once from one file is folded into one edge, one Angular guard on several
        routes likewise (_merge_routes).
    `exclude` is the caller's exclusion rule (an excluded file is neither a mount site nor an import
    target); `files` the registry walk's list of this entry's files (root-relative, already filtered), else
    the tree is walked here. Returns the ORIGINAL list object when nothing changes. Never raises (a failure
    keeps the per-file edges)."""
    try:
        if not edges:
            return edges
        router_rels = {e.src_file for e in edges if e.edge_type == "ROUTES_TO"
                       and (e.attrs or {}).get("framework") in _ROUTER_FRAMEWORKS}
        nest = any(e.edge_type == "ROUTES_TO" and (e.attrs or {}).get("framework") == "nestjs" for e in edges)
        nxt = any(e.edge_type == "ROUTES_TO" and (e.attrs or {}).get("framework") == "nextjs" for e in edges)
        scoped = any(_import_scoped(e.scope) for e in edges)
        out = edges
        root = Path(workspace_root)
        mods = None
        if router_rels or nest or scoped:
            paths: dict = {}
            texts: dict = {}                             # only the files holding a mount word are kept
            mount_rels: list = []
            listing = ((rel, root / rel) for rel in files) if files is not None else _iter_paths(root, exclude)
            for rel, path in listing:
                paths[rel] = path
                if router_rels or nest:
                    text = _read(path)
                    if text is not None and any(w in text for w in _MOUNT_WORDS):
                        texts[rel] = text
                        mount_rels.append(rel)
            cache: dict = {}

            def load(rel):
                if rel not in cache:
                    text = texts.get(rel)
                    if text is None and rel in paths:
                        text = _read(paths[rel])
                    f = analyze(text, rel) if text is not None else None
                    cache[rel] = _RouterFacts(f) if f is not None else None
                return cache[rel]

            def load_module(rel):
                rf = cache.get(rel)
                if rf is not None:
                    return rf.f
                text = texts.get(rel)
                if text is None and rel in paths:
                    text = _read(paths[rel])
                return _File(text, rel) if text is not None and not _skip_file(text, rel) else None

            mods = _Modules(root, set(paths), load_module)
            if router_rels or nest:
                is_test = _test_path_predicate()
                world = _World(load, set(paths), is_test, mods)
                boots: set = set()                       # NestJS bootstrap directories (app roots)
                calls: list = []                         # (directory, setGlobalPrefix values) of each file
                for rel in mount_rels:
                    world.add_mounts(rel)
                    rf = world.facts(rel)
                    if nest and rf is not None and (rf.bootstrap or rf.globals) and not is_test(rel):
                        if rf.bootstrap:
                            boots.add(posixpath.dirname(rel))
                        if rf.globals:
                            calls.append((posixpath.dirname(rel), rf.globals))
                if router_rels and world.mounts_into:
                    out = _recompose(out, world, router_rels)
                if nest and (boots or calls):
                    apps: dict = {d: [] for d in boots}
                    for d, vals in calls:
                        app = _nearest_dir(d, boots)
                        apps.setdefault(d if app is None else app, []).extend(vals)
                    # with one app its root is the bootstrap's package, and a route in another package or in another
                    # application project (whose bootstrap is not seen) gets an app of its own, with no prefix; with
                    # two or more, each app walks up from its bootstrap directory within its package and project
                    route_dirs = {posixpath.dirname(str(e.src_file)) for e in out
                                  if e.edge_type == "ROUTES_TO" and (e.attrs or {}).get("framework") == "nestjs"
                                  and (e.attrs or {}).get("prefix_unresolved") != _NO_CONTROLLER}
                    if len(boots) == 1:
                        (b,) = boots
                        pkg = _project_top(mods, with_projects=False)
                        home = pkg(b) or ""
                        roots = {b: home}
                        app_of = _app_project(mods)
                        for d in route_dirs:
                            other = pkg(d) or ""
                            if other != home:
                                apps.setdefault(other, [])
                                continue
                            r = app_of(d)
                            if r is not None and not _under(b, r):
                                apps.setdefault(r, [])
                    else:
                        roots = _app_roots(boots, route_dirs, _project_top(mods))
                    rooted: dict = {}
                    for d, vals in apps.items():
                        rooted.setdefault(roots.get(d, d), []).extend(vals)
                    out = _global_prefix(out, rooted)
        if nxt:
            out = _next_reanchor(out, root, exclude)
        if scoped and mods is not None:
            out = _resolve_imports(out, mods)
        return _merge_routes(out)
    except Exception as e:  # noqa: BLE001 — a post-pass must never cost the per-file edges
        print(f"[deepmap sync] typescript route post-pass skipped ({type(e).__name__})", file=_sys.stderr)
        return edges


def _recompose(edges: list, world: _World, rels) -> list:
    idx_by_rel: dict = {}
    for i, e in enumerate(edges):
        if e.edge_type == "ROUTES_TO" and e.src_file in rels \
                and (e.attrs or {}).get("framework") in _ROUTER_FRAMEWORKS:
            idx_by_rel.setdefault(e.src_file, []).append(i)
    out = None
    for rel, idxs in idx_by_rel.items():
        rf = world.facts(rel)
        if rf is None:
            continue
        routes = [r for r in rf.routes]
        if len(routes) != len(idxs) or any(edges[i].dst_name != routes[k][2] for k, i in enumerate(idxs)):
            continue                                     # misaligned: leave this file's routes as they are
        for k, i in enumerate(idxs):
            node, paths = routes[k][0], routes[k][1]
            if paths is None:
                continue
            composed = world.route_paths(rel, node, paths)
            new = _composed_attrs(edges[i].attrs or {}, composed)
            if new != (edges[i].attrs or {}):
                if out is None:
                    out = list(edges)
                out[i] = edges[i]._replace(attrs=new)
    return edges if out is None else out


def _global_prefix(edges: list, apps: dict) -> list:
    """Prefix each NestJS route with its app's global prefix. `apps` maps an app directory to the prefixes
    set for it; a route belongs to the nearest app directory at or above its file, and only an app that set
    exactly one non-empty readable prefix applies it. A route on a class with no @Controller gets none (it
    is served by its subclasses, maybe in another app)."""
    out = None
    for i, e in enumerate(edges):
        a = e.attrs or {}
        if e.edge_type != "ROUTES_TO" or a.get("framework") != "nestjs" or "path" not in a \
                or a.get("prefix_unresolved") == _NO_CONTROLLER:
            continue
        app = _nearest_dir(posixpath.dirname(str(e.src_file)), apps)
        vals = set(apps[app]) if app is not None else set()
        prefix = next(iter(vals)) if len(vals) == 1 else None
        if not isinstance(prefix, str) or not prefix.strip("/"):
            continue
        new = dict(a)
        new["path"] = _join_path(prefix, a["path"])
        if a.get("paths"):
            new["paths"] = sorted({_join_path(prefix, p) for p in a["paths"]})
        new["global_prefix"] = prefix
        if out is None:
            out = list(edges)
        out[i] = e._replace(attrs=new)
    return edges if out is None else out


_NEXT_DEP = re.compile(r'"next"\s*:')


def _file_ok(root: Path, rel: str, exclude) -> bool:
    """`rel` is a file of the tree the caller's exclusion rule keeps. Never raises."""
    try:
        return not (exclude is not None and exclude(rel)) and (root / rel).is_file()
    except Exception:  # noqa: BLE001
        return False


def _rooted_next_path(parts: list, r: int, has_dir) -> "str | None":
    """The URL path of the Next.js route file `parts` under the project root parts[:r], or None when it is
    no route there: an app-router `route.*` under <root>/app (else <root>/src/app: Next ignores src/app
    beside a root app/), or a file under <root>/pages/api (else <root>/src/pages/api)."""
    rest = parts[r:]
    base = "/".join(parts[:r])

    def anchor(name):
        if len(rest) > 1 and rest[0] == name:
            return r
        if len(rest) > 2 and rest[0] == "src" and rest[1] == name and not has_dir(posixpath.join(base, name)):
            return r + 1
        return None
    stem = parts[-1].rsplit(".", 1)[0]
    i = anchor("app")
    if i is not None and stem == "route":
        return _next_path(parts[i + 1:-1])
    j = anchor("pages")
    if j is not None and j + 1 < len(parts) - 1 and parts[j + 1] == "api":
        return _next_path(parts[j + 1:-1] + ([] if stem == "index" else [stem]))
    return None


def _next_reanchor(edges: list, root: Path, exclude) -> list:
    """Anchor each Next.js route at its project root: the nearest directory above its file holding a
    next.config.* or a package.json that depends on next. The per-file reader takes the first `app` /
    `pages` directory of the path, which is wrong when a monorepo package is itself named `app`
    (next-forge's apps/app/app/...). A route file under a root but outside its app / pages/api directory is
    dropped; a file with no root above it keeps the per-file path. Only stats files near the routes."""
    roots: dict = {}
    dirs: dict = {}

    def has_dir(d):
        if d not in dirs:
            try:
                dirs[d] = (root / d).is_dir()
            except OSError:
                dirs[d] = False
        return dirs[d]

    def is_root(d):
        if d not in roots:
            got = any(_file_ok(root, posixpath.join(d, n) if d else n, exclude) for n in sorted(_NEXT_CONFIGS))
            pkg = posixpath.join(d, "package.json") if d else "package.json"
            if not got and _file_ok(root, pkg, exclude):
                # weaker evidence: a package depending on next is a root only beside an app / pages directory
                # (a monorepo root that lists next must not swallow a sub-folder app's routes)
                text = _read(root / pkg)
                got = bool(text) and _NEXT_DEP.search(text) is not None and any(
                    has_dir(posixpath.join(d, x) if d else x) for x in ("app", "src/app", "pages", "src/pages"))
            roots[d] = got
        return roots[d]

    out = None
    drop = set()
    for i, e in enumerate(edges):
        a = e.attrs or {}
        if e.edge_type != "ROUTES_TO" or a.get("framework") != "nextjs":
            continue
        parts = str(e.src_file).split("/")
        r = next((k for k in range(len(parts) - 1, -1, -1) if is_root("/".join(parts[:k]))), None)
        if r is None:
            continue                                     # no project root above it: the per-file path stays
        path = _rooted_next_path(parts, r, has_dir)
        if path is None:
            drop.add(i)
        elif path != a.get("path"):
            if out is None:
                out = list(edges)
            out[i] = e._replace(attrs=dict(a, path=path))
    if out is None and not drop:
        return edges
    src = edges if out is None else out
    return [e for k, e in enumerate(src) if k not in drop]
