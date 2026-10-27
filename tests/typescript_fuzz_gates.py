#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TypeScript / JavaScript recognizer hardening — 300-source fuzz + mutation fuzz + metamorphic + the
integration MONEY GATE (no Neo4j).

Mirrors the Rails/C#/Java/Go fuzz suites: the recognizer must NEVER raise, be deterministic, and only ever
emit its declared edge vocabulary over adversarial input — here also unbalanced brackets, unterminated
strings / templates / regexes / comments, JSX text, CR-only line endings and 2000+ char lines; mutating the
real fixture app (cutting, duplicating and inserting characters) must degrade the same way; its finalize
post-pass must survive any edge list over any tree, and mutated tsconfig / package.json files without
falling back; the tsconfig-style JSON reader reads comments and trailing commas as plain JSON; and the whole
recognizer -> validate -> merge -> resolve -> prune -> push chain must run through a FAKE driver with no
Neo4j.

Usage: python tests/typescript_fuzz_gates.py [--root <tree> | --root=<tree>]   (--root: load build/ from another
       checkout — the RED-first run; a missing value or a tree without build/ is a usage error)
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import random
import string
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _parse_root(args: list) -> tuple:
    root, it = None, iter(args)
    for arg in it:
        if arg == "--root":
            root = next(it, "")
        elif arg.startswith("--root="):
            root = arg[len("--root="):]
        else:
            return None, f"unknown argument {arg!r}"
        if not root or not (Path(root) / "build").is_dir():
            return None, f"--root needs a directory that holds build/ (got {root!r})"
    return (Path(root).resolve() if root else REPO), None


ROOT, _USAGE = _parse_root(sys.argv[1:]) if __name__ == "__main__" else (REPO, None)
if _USAGE:
    print(f"usage: python tests/typescript_fuzz_gates.py [--root <tree>]  ({_USAGE})", file=sys.stderr)
    raise SystemExit(2)
BUILD = ROOT / "build"
sys.path.insert(0, str(BUILD))

import framework_edges as FE       # noqa: E402
import language_recognizers as LR  # noqa: E402
import sync as S                   # noqa: E402

try:
    import typescript_edges as T   # noqa: E402
except ImportError:                # the pre-feature tree: RED, never a crash
    T = None

_PASS = _FAIL = 0


def check(desc: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    ok = bool(cond)
    _PASS += ok
    _FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc}" + ("" if ok else f"  -- {str(detail)[:700]}"))


def _app() -> dict:
    """The fixture app of tests/typescript_edges_gates.py (real framework code to mutate)."""
    spec = importlib.util.spec_from_file_location("dmts_gates_app", REPO / "tests" / "typescript_edges_gates.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.APP


# ---- fuzz corpus -------------------------------------------------------------
_FRAGMENTS = [
    "import { Controller, Get, Post, Injectable, Module } from '@nestjs/common';",
    "import { Entity, ManyToOne, OneToMany, JoinColumn } from 'typeorm';",
    "import { CatsService } from './cats.service';", "const express = require('express');",
    "const { listUsers } = require('./users');", "import * as users from '../controllers/users';",
    "@Controller('cats')", "@Controller({ path: 'dogs', version: '1' })", "@Injectable()", "@Entity()",
    "@Module({ imports: [CatsModule, forwardRef(() => X)], providers: [{ provide: T, useClass: Y }] })",
    "export class CatsController {", "export class Order extends Base implements IOrder {",
    "export default class extends Mixin(A, B) {", "export interface Shape extends A, B {",
    "abstract class Q<T extends R = S> extends P<T> {", "  constructor(private readonly s: CatsService) {}",
    "  constructor(@Inject('TOKEN') private x: number, @InjectRepository(User) r: Repository<User>) {}",
    "  @Get(':id')", "  @Post()", "  @Get(['a', 'b'])", "  @UseGuards(AuthGuard, new RolesGuard())",
    "  @Cron(CronExpression.EVERY_HOUR)", "  @Interval(5000)", "  @OnEvent('x.y')", "  @MessagePattern({ cmd: 1 })",
    "  findOne(@Param('id') id: string): Promise<Cat> {", "  async *gen<T>(x: T): AsyncGenerator<T> {",
    "  @ManyToOne(() => User, (u) => u.items, { onDelete: 'CASCADE' })", "  @JoinColumn()", "  user: User;",
    "  @OneToMany(() => Item, (i) => i.order, { cascade: true })", "  items: Item[];",
    "  handler = async (req) => { return req; };", "  static { init(); }", "  get value(): number { return 1; }",
    "  #secret() {}", "  'quoted'() {}", "  [Symbol.iterator]() {}", "  return this.s.find();", "}",
    "const router = express.Router();", "const app = express();", "const r2 = new Router({ prefix: '/v1' });",
    "router.get('/', listUsers);", "router.post('/x', auth, asyncHandler(create));",
    "router.route('/:id').get(getOne).put(update);", "app.use('/api', router);", "app.use(router.routes());",
    "fastify.route({ method: 'GET', url: '/p', handler: ping });", "app.register(plugin, { prefix: '/i' });",
    "api.on(['PUT'], '/p/:id', h);", "const api = new Hono().basePath('/api');", "api.route('/b', books);",
    "export async function GET(req: Request) {", "export const POST = async () => {};",
    "export default function handler(req, res) {", "module.exports = router;", "exports.list = (req, res) => 1;",
    "export const routes: Routes = [{ path: 'a', component: A, children: [{ path: 'b', component: B }] }];",
    "cache.get('/users');", "axios.get('/x', cfg);", "const s = '@Get(\"/x\") router.get(\"/y\", h)';",
    "const t = `@Controller('t') ${ x } router.get('/t', h)`;", "// @Get('/c') router.get('/c', h)",
    "/* @Post('/b') */", "const re = /router\\.get\\('\\/x'/g;", "const d = a / b / c;", "x = y ? /a/ : /b/;",
    "return <div className=\"x\">Don't {router.get('/j', h)}</div>;", "app.setGlobalPrefix('api');",
    "import type { Router } from 'express';", "import { FastifyInstance } from 'fastify';",
    "export function mount(r: Router, srv?: FastifyInstance) {", "  public router!: Router;",
    "  constructor(private readonly users: UsersController, private api: Router) {}",
    "  private readonly users = new UsersController();", "  public path = '/users';",
    "  getAll = asyncHandler(async (req, res) => { return res; });", "const API = '/api';",
    "const BASE = `${API}/v1`;", "  this.router.get(`${BASE}${this.path}/:id`, this.users.getOne);",
    "  this.api.post(this.path, this.users.create.bind(this.users));", "srv.get(`${this.path}`, h);",
    "import * as h from '#src/handlers.js';", "import { Base } from '#lib';", "router.get('/h', h.list);",
    "for (const p of ['/a', '/b']) router.get(p, h);", "for (let k in m) { app.use(`/${k}`, r); }",
    "let a, b = '/x', { c } = d;", "var base;", "const API2 = '/v2', V = `${API2}/x`;",
    "const l = n > 0 ? `${n} items</span>` : '';", "return <p><b>Note</b> wrap ` here</p>;",
    "export { default as users } from './users';", "export default new UsersController();",
]
_JUNK = ["\x00\x01\xff", "日本語のテキスト", "🚀🔥", "a" * 1500, "@", "@@", "@(", "class", "class {", "{", "}", "(", ")",
         "[", "]", "`", "${", "'", '"', "/", "/*", "*/", "//", "=>", "<", ">", "</", "\\", "\r\n\r\r\n",
         "router.get(", ".get(", "@Get(", "new ", "import {", "from '", "require(", "export default",
         "constructor(", "＜クラス＞", "-- not ts --", "{\"json\": true}", "#!/usr/bin/env node"]


def _fuzz_source(rng: random.Random) -> str:
    n = rng.randint(0, 50)
    lines = []
    for _ in range(n):
        r = rng.random()
        if r < 0.6:
            lines.append(rng.choice(_FRAGMENTS))
        elif r < 0.8:
            lines.append(rng.choice(_JUNK))
        else:
            lines.append("".join(rng.choice(string.printable) for _ in range(rng.randint(0, 80))))
    if rng.random() < 0.05:
        lines.append("x" * rng.randint(1990, 2100))         # around the minified-line threshold
    return rng.choice(["\n", "\r\n", "\r"]).join(lines)


def _vocab_ok(entry, out) -> bool:
    return all(e.edge_type in entry.edge_types and e.src_name and e.dst_name and e.conf_hint in
               ("LOCAL", "NAME", "DYNAMIC") and isinstance(e.attrs, dict) for e in out[1])


def gate_fuzz() -> None:
    print("\n-- 300 seeded adversarial sources: never raise, deterministic, vocab-clean --")
    if T is None:
        check("build/typescript_edges.py exists (the recognizer)", False, "ImportError")
        return
    rng = random.Random(20261004)
    entry = next((e for e in LR.REGISTRY if e.name == "typescript"), None)
    check("the registry has the typescript entry", entry is not None)
    if entry is None:
        return
    crashed = nondet = badvocab = badshape = badnode = emitted = 0
    first_crash = ""
    exts = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
    for i in range(300):
        src = _fuzz_source(rng)
        rel = f"src/app/api/f{i}/route{exts[i % 6]}" if i % 7 == 0 else f"src/f{i}{exts[i % 6]}"
        try:
            out1 = T.recognize(src, rel)
            out2 = T.recognize(src, rel)
        except Exception as exc:  # noqa: BLE001
            crashed += 1
            first_crash = first_crash or f"{rel}: {type(exc).__name__}: {exc}"
            continue
        if not (isinstance(out1, tuple) and len(out1) == 2
                and isinstance(out1[0], list) and isinstance(out1[1], list)):
            badshape += 1
            continue
        if out1 != out2:
            nondet += 1
        emitted += len(out1[1])
        if not _vocab_ok(entry, out1):
            badvocab += 1
        if len(LR.validate_supplemental(out1[0], "typescript")) != len(out1[0]):
            badnode += 1
    check("0/300 raised", crashed == 0, f"{crashed} crashed; first: {first_crash}")
    check("0/300 wrong return shape", badshape == 0, f"{badshape}")
    check("0/300 nondeterministic", nondet == 0, f"{nondet}")
    check("0/300 emitted an undeclared edge type / an empty endpoint / an unknown conf hint", badvocab == 0,
          f"{badvocab}")
    check("0/300 produced a validator-rejected supplemental node", badnode == 0, f"{badnode}")
    check("the corpus exercises the reader (>= 50 edges emitted over the 300 sources)", emitted >= 50, emitted)
    # A stray backtick leaves a class body (and a property's call) unclosed: a span then ends one past the
    # code. Found by the mutation fuzz; pinned here so no seed sequence is needed to reach it.
    raised = []
    for src in ("export class C {\n  getOne = h(async () => {\n `\n  });\n}\n",
                "export class C {\n  router = Router(\n `\n}\n", "class C { a = (`"):
        try:
            T.recognize(src, "src/c.ts")
        except Exception as exc:  # noqa: BLE001
            raised.append(f"{src[:24]!r}: {type(exc).__name__}")
    check("an unterminated template that leaves a class body unclosed never raises", not raised, raised)


def _mutate(rng: random.Random, text: str) -> str:
    """Cut, duplicate or insert a few characters / spans of real framework code."""
    s = text
    for _ in range(rng.randint(1, 6)):
        if not s:
            break
        i = rng.randrange(len(s))
        op = rng.random()
        if op < 0.35:
            s = s[:i] + s[i + rng.randint(1, 40):]
        elif op < 0.6:
            j = min(len(s), i + rng.randint(1, 60))
            s = s[:j] + s[i:j] + s[j:]
        else:
            s = s[:i] + rng.choice(["'", '"', "`", "${", "/", "/*", "//", "{", "}", "(", ")", "[", "]", "@",
                                    "\n", "<", ">", "\\", "=>"]) + s[i:]
    return s


def gate_mutation_fuzz() -> None:
    print("\n-- 300 mutations of the real fixture app: never raise, deterministic, vocab-clean --")
    if T is None:
        check("build/typescript_edges.py exists (the recognizer)", False, "ImportError")
        return
    app = _app()
    rels = sorted(app)
    entry = next((e for e in LR.REGISTRY if e.name == "typescript"), None)
    rng = random.Random(4100)
    crashed = nondet = badvocab = emitted = 0
    first = ""
    for k in range(300):
        rel = rels[k % len(rels)]
        src = _mutate(rng, app[rel])
        try:
            a = T.recognize(src, rel)
            b = T.recognize(src, rel)
        except Exception as exc:  # noqa: BLE001
            crashed += 1
            first = first or f"{rel}: {type(exc).__name__}: {exc}"
            continue
        nondet += a != b
        emitted += len(a[1])
        badvocab += (entry is None) or not _vocab_ok(entry, a)
    check("0/300 mutated framework files raised", crashed == 0, f"{crashed}; first: {first}")
    check("0/300 nondeterministic", nondet == 0, f"{nondet}")
    check("0/300 left the declared vocabulary", badvocab == 0, f"{badvocab}")
    check("the mutations still exercise the reader (>= 100 edges emitted)", emitted >= 100, emitted)


def gate_finalize_fuzz() -> None:
    print("\n-- finalize: any edge list over any tree degrades, never raises --")
    if T is None:
        check("build/typescript_edges.py exists (the recognizer)", False, "ImportError")
        return
    rng = random.Random(77)
    app = _app()
    raised = 0
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for k, (rel, text) in enumerate(sorted(app.items())):
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(_mutate(rng, text) if k % 2 else text, encoding="utf-8")
        edges = []
        for rel, text in sorted(app.items()):
            edges.extend(T.recognize(text, rel)[1])
        for trial in range(20):
            sample = [rng.choice(edges) for _ in range(rng.randint(0, 40))] if edges else []
            if trial % 4 == 0:     # attrs a sync could never produce
                sample = [e._replace(attrs={"framework": rng.choice(["express", "nestjs", None, 7]),
                                            "path": rng.choice([None, 5, "", "/x"])}) for e in sample]
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    out = T.finalize(root, sample, exclude=lambda rel: rng.random() < 0.2)
                if not isinstance(out, list):
                    raised += 1
            except Exception:  # noqa: BLE001
                raised += 1
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                out = T.finalize(root / "does-not-exist", edges, exclude=None)
            ok = isinstance(out, list) and len(out) <= len(edges)
        except Exception:  # noqa: BLE001
            ok = False
    check("20 random edge lists over a half-mutated tree: finalize never raises, always returns a list",
          raised == 0, f"{raised}")
    check("finalize over a missing root keeps the per-file edges (a list, never more edges)", ok)


TSCONFIG = {"compilerOptions": {"baseUrl": "./", "paths": {"@app/*": ["src/app/*"], "@lib": ["libs/lib/src"]},
                                "strict": True}, "extends": "../tsconfig.base.json", "include": ["src/**/*.ts"]}


def gate_config_fuzz() -> None:
    print("\n-- tsconfig / package.json reading: comments, trailing commas, garbage — never raises --")
    jsonc = getattr(T, "_jsonc", None) if T is not None else None
    if jsonc is None:
        check("the tsconfig-style JSON reader exists (typescript_edges._jsonc)", False, "missing")
        return
    import json as _json
    plain = _json.dumps(TSCONFIG, indent=2)
    commented = ("// a Nest monorepo\n{\n  /* the compiler */ \"compilerOptions\": {\n    \"baseUrl\": \"./\", // root\n"
                 "    \"paths\": {\n      \"@app/*\": [\"src/app/*\",],\n      \"@lib\": [\"libs/lib/src\"],\n    },\n"
                 "    \"strict\": true,\n  },\n  \"extends\": \"../tsconfig.base.json\",\n  \"include\": [\"src/**/*.ts\"],\n}\n")
    check("comments, trailing commas and `//` inside strings read as the same object as plain JSON",
          jsonc(plain) == TSCONFIG == jsonc(commented)
          and jsonc('{ "a": "http://x/*y*/", "b": [1, 2,], }') == {"a": "http://x/*y*/", "b": [1, 2]},
          (jsonc(commented), jsonc('{ "a": "http://x/*y*/", "b": [1, 2,], }')))
    rng = random.Random(9090)
    raised = bad = 0
    for _ in range(300):
        s = _mutate(rng, rng.choice([plain, commented]))
        try:
            v = jsonc(s)
        except Exception:  # noqa: BLE001
            raised += 1
            continue
        bad += not (v is None or isinstance(v, dict))
    check("300 mutated configs: the reader never raises and returns an object or None", raised == 0 and bad == 0,
          (raised, bad))
    app = _app()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for rel, text in sorted(app.items()):
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        edges = []
        for rel, text in sorted(app.items()):
            edges.extend(T.recognize(text, rel)[1])
        raised = 0
        for k in range(12):
            for rel, base in (("nest/tsconfig.json", commented), ("tsconfig.json", plain),
                              ("packages/x/package.json", '{ "name": "@acme/x", "main": "src/index.ts", "exports": '
                                                          '{ ".": { "types": "./src/index.ts" } } }'),
                              ("subpath/package.json", '{ "name": "subpath-app", "imports": { "#src/*": "./src/*", '
                                                       '"#db": { "types": "./src/db.ts", "default": ["./dist/db.js"] }, '
                                                       '"#dep": "some-package", "#bad/*": null } }')):
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(_mutate(rng, base) if k else base, encoding="utf-8")
            err = io.StringIO()
            try:
                with contextlib.redirect_stderr(err):
                    out = T.finalize(root, edges)
                raised += not isinstance(out, list) or "skipped" in err.getvalue()
            except Exception:  # noqa: BLE001
                raised += 1
    check("finalize over the fixture app with 12 rounds of mutated tsconfig / package.json files never fails "
          "(not even into its keep-the-per-file-edges fallback)", raised == 0, raised)


CTRL = """import { Controller, Get, Post } from '@nestjs/common';
import { CatsService } from './cats.service';

@Controller('cats')
export class CatsController {
  constructor(private readonly cats: CatsService) {}

  @Get(':id')
  findOne() {
    return this.cats.findOne();
  }

  @Post()
  create() {
    return this.cats.create();
  }
}
"""


def gate_metamorphic() -> None:
    print("\n-- metamorphic properties --")
    if T is None:
        check("build/typescript_edges.py exists (the recognizer)", False, "ImportError")
        return
    rel = "src/cats.controller.ts"
    n0, e0 = T.recognize(CTRL, rel)
    check("idempotent (recognize twice == same)", T.recognize(CTRL, rel) == (n0, e0))
    commented = CTRL.replace("  @Get(':id')", "  // @Delete('/nope') router.get('/x', h)\n  /* @Put() */ @Get(':id')")
    _, e1 = T.recognize(commented, rel)
    check("comment insertion is edge-invariant (commented decorators / routes add nothing)", e1 == e0,
          f"{e1} vs {e0}")
    stringed = CTRL.replace("  @Post()", "  label = '@Get(\"/s\")';\n  tpl = `@Delete('/t') ${1}`;\n  @Post()")
    _, e2 = T.recognize(stringed, rel)
    check("decorator text inside strings / template literals adds nothing", e2 == e0, f"{e2} vs {e0}")
    shifted = "\n\n\n" + CTRL
    n3, e3 = T.recognize(shifted, rel)
    check("blank-line prefix shifts start_lines by exactly 3; names/edges identical",
          e3 == e0 and all(a["start_line"] + 3 == b["start_line"] for a, b in zip(n0, n3)))
    crlf = CTRL.replace("\n", "\r\n")
    n4, e4 = T.recognize(crlf, rel)
    check("CRLF line endings: identical edges and start lines", e4 == e0
          and [x["start_line"] for x in n4] == [x["start_line"] for x in n0])
    no_post = CTRL.replace("  @Post()\n", "")
    _, e5 = T.recognize(no_post, rel)
    check("removing a route decorator drops exactly its route (no phantom edge)",
          [x for x in e0 if x.dst_name != "CatsController.create"] == e5, f"{e5}")
    one_line = " ".join(CTRL.split())
    _, e6 = T.recognize(one_line, rel)
    check("the same controller on ONE line (no newlines) yields the same edges",
          {(x.edge_type, x.src_name, x.dst_name) for x in e6} == {(x.edge_type, x.src_name, x.dst_name) for x in e0},
          f"{e6}")


class _Sess:
    def __init__(self, log):
        self.log = log

    def run(self, cypher, **params):
        for row in params.get("rows") or [params]:
            self.log.append((cypher, row))

    def begin_transaction(self):
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


ENTITY = """import { Entity, ManyToOne, OneToMany } from 'typeorm';
import { Owner } from './owner.entity';
import { Toy } from './toy.entity';

@Entity()
export class Cat {
  @ManyToOne(() => Owner, (o) => o.cats, { onDelete: 'CASCADE' })
  owner: Owner;

  @OneToMany(() => Toy, (t) => t.cat, { cascade: true })
  toys: Toy[];
}
"""
OWNER = "import { Entity } from 'typeorm';\n\n@Entity()\nexport class Owner {}\n"
TOY = "import { Entity } from 'typeorm';\n\n@Entity()\nexport class Toy {}\n"
ROUTES = """const express = require('express');
const router = express.Router();

function listCats(req, res) {
  res.json([]);
}

router.get('/cats', listCats);
module.exports = router;
"""
CTL = """import { asyncHandler } from '../utils/async';

export class DogsController {
  list = asyncHandler(async (req, res) => {
    res.json([]);
  });
}
"""
DOG_ROUTES = """import { Router } from 'express';
import { DogsController } from '../controllers/dogs.controller';

export class DogsRoute {
  public path = '/dogs';
  public router = Router();
  private dogs = new DogsController();

  constructor() {
    this.router.get(`${this.path}`, this.dogs.list);
  }
}
"""


def gate_money() -> None:
    print("\n-- MONEY GATE: TS tree -> _apply_recognizers -> push_to_neo4j (fake driver) --")
    if T is None:
        check("build/typescript_edges.py exists (the recognizer)", False, "ImportError")
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for rel, src in (("src/cat.entity.ts", ENTITY), ("src/owner.entity.ts", OWNER),
                         ("src/toy.entity.ts", TOY), ("src/routes.js", ROUTES),
                         ("src/controllers/dogs.controller.ts", CTL), ("src/routes/dogs.route.ts", DOG_ROUTES)):
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(src, encoding="utf-8")
        merged, typed = S._apply_recognizers(root, [])
        check("the recognizer fills the empty entity layer (ensured :Class nodes) and the wrapped handler "
              "property (a :Method node)",
              {n["name"] for n in merged} >= {"Cat", "Owner", "Toy", "DogsController.list"},
              str({n.get("name") for n in merged}))
        log: list = []
        n_nodes, _ = S.push_to_neo4j(_Drv(log), merged, [], [], typed)
        node_merges = [c for c, _ in log if "MERGE (x:Class" in c or "MERGE (x:Method" in c]
        check("every ensured node MERGEs with its label (:Class, :Method for a handler property)",
              n_nodes == len(merged) and len(node_merges) == len(merged),
              f"{n_nodes} nodes, {len(node_merges)} :Class/:Method merges")
        cas = sorted((p["b"].split("::")[1].split(":")[0], p["a"].split("::")[1].split(":")[0])
                     for c, p in log if "MERGE" in c and "[r:CASCADES]" in c and "MERGE (b)-[r:CASCADES]->(a)" in c)
        check("both TypeORM cascades persist REVERSED: (Cat)->(Owner) for onDelete, (Toy)->(Cat) for cascade",
              cas == [("Cat", "Owner"), ("Toy", "Cat")], str(cas))
        routes = [(c, p) for c, p in log if "MERGE" in c and "ROUTES_TO" in c]
        check("ROUTES_TO to a handler no extractor node-ified DROPS (SAME_FILE_STRICT, LOCAL)", routes == [],
              str(routes))
        engine = [
            {"key": "routes_mod", "label": "Module", "name": "routes", "source_file": "src/routes.js",
             "start_line": 1, "documented": False, "body_summary": "m"},
            {"key": "listcats", "label": "Function", "name": "listCats", "source_file": "src/routes.js",
             "start_line": 4, "documented": False, "body_summary": "f"},
            {"key": "dogs_route_mod", "label": "Module", "name": "dogs.route",
             "source_file": "src/routes/dogs.route.ts", "start_line": 1, "documented": False, "body_summary": "m"},
        ]
        log2: list = []
        S.push_to_neo4j(_Drv(log2), merged + engine, [], [], typed)
        routes2 = {p.get("b", "").split("::")[-1]: p for c, p in log2 if "MERGE" in c and "ROUTES_TO" in c}
        cats = routes2.get("listcats", {})
        check("with the engine's handler :Function present, the SAME route resolves EXTRACTED with its path",
              cats.get("c") == "EXTRACTED" and cats.get("attrs", {}).get("path") == "/cats", str(routes2))
        dogs = routes2.get("DogsController.list:4", {})
        check("a class-based route (`this.dogs.list`, `${this.path}`) resolves onto the recognizer's own "
              "wrapped-handler :Method node, INFERRED (a cross-file name), path /dogs",
              len(routes2) == 2 and dogs.get("c") == "INFERRED" and dogs.get("attrs", {}).get("path") == "/dogs",
              str(routes2))
        pruned = {c.split(":")[1].split("]")[0] for c, _ in log if "DELETE r" in c and "[r:" in c}
        check("every whitelisted edge type pruned on this sync (anti-phantom, full vocab)",
              pruned >= (set(FE.ALLOWED_EDGE_TYPES) - {"IMPORTS"}), str(sorted(pruned)))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — TypeScript / JavaScript recognizer fuzz + metamorphic + money gates (no Neo4j)")
    print("=" * 72)
    with contextlib.redirect_stderr(io.StringIO()):
        for gate in (gate_fuzz, gate_mutation_fuzz, gate_finalize_fuzz, gate_config_fuzz, gate_metamorphic, gate_money):
            try:
                gate()
            except Exception as exc:  # noqa: BLE001 — a crash on a pre-feature tree is a RED result
                check(f"{gate.__name__} ran without raising", False, f"{type(exc).__name__}: {exc}")
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
