#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Typed-receiver call linking — the TypeScript / JavaScript reader gates (receiver-calls design slice S3c: §3.2,
§3.3, §5 TS/JS, §9 S3c). No Neo4j, no network, no spend; real-engine runs use a temporary DEEPMAP_HOME.

build/receiver_facts_ts.py lowers TS / JS sources (tree-sitter, through the sync's shared graphify_extract._Source)
to the frozen IR of build/typed_calls.py and places imports with typescript_edges.modules_for (the rule typed-edge
placement uses). These gates drive it end to end — graphify's REAL graph.json of the fixture trees, captured on
graphify 0.8.39 AND 0.8.51, through map_graph, sync._apply_recognizers and sync._unkey_receiver_calls into
typed_calls.link — and pin:
  A  the module contract: stdlib-only imports at import time, Python 3.10 syntax, the registry wiring, and
     typescript_edges.modules_for as an additive twin of _Modules.resolve that recognize / finalize never call;
  B  the grammar canary per language (a broken grammar disables that language alone);
  C  the IR the reader writes (params, locals, poisoning, fields, `this`, type parameters, decorators, imports,
     the export tables, the census and its parse-error rule, decls, patches, open / dynamic classes, arity);
  D  the binder (named / default / namespace / CommonJS imports, barrels and `export *` ambiguity, tsconfig paths,
     NodeNext `.js` names, workspace packages, packages as external, module(rel) only for a plain module);
  E  every §3.2 / §3.3 shape and every §5 TS/JS must-not shape end to end on BOTH engines' captures: the exact
     typed set and grades, the engine tuples REPLACE removes and keeps, injected name tuples (as an engine
     fan-out would give) removed only where every site is settled, the test-caller policy, the zod `this.`
     rebind, NestJS constructor parameter properties, Angular inject(), a Python `Order` never reached, the
     flags (off -> the same list; one language; one shape; REPLACE=0), idempotence and engine parity;
     The phase-2 review's trees: ts_casts (a write through a type assertion — `(x as any) = v`, `(<any>x) = v`,
     `(this as any).f = v` — still rebinds x / this.f, so the engine's tuple of a call on it stays) and js_nest
     (graphify's symbol pass gives a root `function` / `const f = () =>` the bare `Foo()` of its nested arrow /
     declaration: the core keeps that tuple; a class method's nested arrow is detached and REPLACEd);
  F  the INSTALLED engine's fresh extraction of every tree gives exactly the captured result;
  H  shapes whose fix is a rule of the core (static vs instance members — LanguageSpec.static_split; an attr
     patched onto a builtin — PATCH_RULES loose; a mixin no file reads — PATCH_RULES star): the core must declare
     each rule, and each shape must hold;
  I  dynamic dispatch on the real engine (the ts_dispatch tree): a project OVERRIDE a declared receiver
     (`r: Base`) or `this` may reach at run time stays reachable (re-emitted at AMBIGUOUS, never upgraded; in-file
     and cross-file subclasses, TS and JS), while an unrelated same-named method and a constructed receiver's
     subclass are removed; and the real engine's tuples of a builtin prototype the project patches stay (the
     ts_globals tree);
  K  an absent tree-sitter binding / grammar (an OPTIONAL dependency): the canary fails, the language is disabled,
     link() returns its input unchanged;
  G  mutation teeth: each reader rule broken in a copy of the module turns its section RED.
The second must-not pair (ts_mustnot2 / js_mustnot2, the S3c review round) pins: path aliases no config maps
(`@utils/x`, `$lib`) and package.json-declared dependencies, `export *` beside a private local, local `type` /
`import X = N.Y` shadowing, `@ts-nocheck` / `@ts-ignore` (the core's unchecked rule), computed member names,
patched globals and builtins, field writes through a `this` alias / a `.bind(this)` callback / (JavaScript) any
object, cross-file prototype mixins, binding forms that shadow an outer typed name, JavaScript arity and a
receiver deeper than the IR allows.

Fixtures: tests/fixtures/typed_calls/<0839|0851>/ts/<tree>.json — the REAL graph.json graphify writes for TREES[<tree>]
(`python tests/typed_calls_ts_gates.py --capture` writes every capture of the installed engine's bucket that is
missing or was made from another tree, and keeps the current ones; run it under both engines after editing a tree).

Usage: python tests/typed_calls_ts_gates.py [--root <tree>] [--capture]
       (--root: load build/ from another checkout — the RED-first run)
"""
from __future__ import annotations

import ast
import contextlib
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _parse_args(args: list) -> tuple:
    root, capture, it = None, False, iter(args)
    for arg in it:
        if arg == "--capture":
            capture = True
        elif arg == "--root":
            root = next(it, "")
            if not root or not (Path(root) / "build").is_dir():
                return None, False, f"--root needs a directory that holds build/ (got {root!r})"
        elif arg.startswith("--root="):
            root = arg[len("--root="):]
            if not root or not (Path(root) / "build").is_dir():
                return None, False, f"--root needs a directory that holds build/ (got {root!r})"
        else:
            return None, False, f"unknown argument {arg!r}"
    return (Path(root).resolve() if root else REPO), capture, None


ROOT, CAPTURE, _USAGE = _parse_args(sys.argv[1:]) if __name__ == "__main__" else (REPO, False, None)
if _USAGE:
    print(f"usage: python tests/typed_calls_ts_gates.py [--root <tree>] [--capture]  ({_USAGE})", file=sys.stderr)
    raise SystemExit(2)
BUILD = ROOT / "build"
FX = REPO / "tests" / "fixtures" / "typed_calls"
BUCKETS = {"0.8.39": "0839", "0.8.51": "0851"}
sys.path.insert(0, str(BUILD))

_IMPORT_ERRORS: dict = {}


def _try_import(name):
    try:
        return importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001 — the pre-feature tree: RED, never a crash
        _IMPORT_ERRORS[name] = repr(exc)
        return None


for _k in [k for k in os.environ if k.startswith("DEEPMAP_")]:
    os.environ.pop(_k, None)                      # the gate's own flags only (a stray operator flag changes graphs)

TC = _try_import("typed_calls")
R = _try_import("receiver_facts_ts")
GX = _try_import("graphify_extract")
S = _try_import("sync")
TE = _try_import("typescript_edges")


class Rec:
    """Check recorder. loud=True prints every check; loud=False only counts (mutation runs)."""

    def __init__(self, loud: bool = True):
        self.loud, self.passed, self.failed, self.fails = loud, 0, 0, []

    def check(self, desc: str, cond, detail="") -> bool:
        ok = bool(cond)
        if ok:
            self.passed += 1
        else:
            self.failed += 1
            self.fails.append(desc)
        if self.loud:
            print(f"  [{'PASS' if ok else 'FAIL'}] {desc}" + ("" if ok else f"  -- {str(detail)[:900]}"))
        return ok


# ================================================================================================
# The fixture trees (each captured on graphify 0.8.39 and 0.8.51)
# ================================================================================================

TS_APP = {
    'tsconfig.json': (
        '{\n'
        '  "compilerOptions": {\n'
        '    "baseUrl": ".",\n'
        '    "paths": {\n'
        '      "@lib/*": ["src/lib/*"],\n'
        '      "@a/*": ["packages/a/*"]\n'
        '    }\n'
        '  }\n'
        '}\n'
    ),
    'src/models/base.ts': (
        'export class Base {\n'
        '  helper(): number {\n'
        '    return 1;\n'
        '  }\n'
        '}\n'
    ),
    'src/models/order.ts': (
        'import { Base } from "./base";\n'
        '\n'
        'export class Order extends Base {\n'
        '  total = 0;\n'
        '\n'
        '  save(): void {\n'
        '    this.helper();\n'
        '  }\n'
        '\n'
        '  static create(): Order {\n'
        '    return new Order();\n'
        '  }\n'
        '\n'
        '  archive(): void {\n'
        '    super.helper();\n'
        '  }\n'
        '\n'
        '  static clone(): Order {\n'
        '    return this.create();\n'
        '  }\n'
        '}\n'
    ),
    'src/models/repo.ts': (
        'import { Order } from "./order";\n'
        '\n'
        'export interface IRepo {\n'
        '  find(id: number): Order;\n'
        '}\n'
        '\n'
        'export class SqlRepo implements IRepo {\n'
        '  find(id: number): Order {\n'
        '    return new Order();\n'
        '  }\n'
        '}\n'
        '\n'
        'export class Cache {\n'
        '  find(id: number): Order | null {\n'
        '    return null;\n'
        '  }\n'
        '}\n'
    ),
    'src/models/index.ts': (
        'export * from "./order";\n'
        'export { SqlRepo, Cache } from "./repo";\n'
        'export type { IRepo } from "./repo";\n'
    ),
    'src/lib/util.ts': (
        'export function fmt(x: number): string {\n'
        '  return String(x);\n'
        '}\n'
        '\n'
        'export const twice = (x: number): number => x * 2;\n'
    ),
    'src/schemas.ts': (
        'export class ZodNumber {\n'
        '  _addCheck(c: number): ZodNumber {\n'
        '    return this;\n'
        '  }\n'
        '\n'
        '  multipleOf(v: number): ZodNumber {\n'
        '    return this._addCheck(v);\n'
        '  }\n'
        '}\n'
        '\n'
        'export class ZodDate {\n'
        '  _addCheck(c: number): ZodDate {\n'
        '    return this;\n'
        '  }\n'
        '\n'
        '  min(v: number): ZodDate {\n'
        '    return this._addCheck(v);\n'
        '  }\n'
        '\n'
        '  stray(): void {\n'
        '    this.multipleOf(1);\n'
        '  }\n'
        '}\n'
    ),
    'src/service.ts': (
        'import { Order, SqlRepo, Cache } from "./models";\n'
        'import { IRepo } from "./models/repo";\n'
        'import * as util from "@lib/util";\n'
        'import * as path from "path";\n'
        '\n'
        'export class OrderService {\n'
        '  private cache = new Cache();\n'
        '  repo2: SqlRepo;\n'
        '\n'
        '  constructor(private readonly repo: SqlRepo) {\n'
        '    this.repo2 = new SqlRepo();\n'
        '  }\n'
        '\n'
        '  handle(o: Order, maybe?: Order, n: Order | null = null): void {\n'
        '    o.save();\n'
        '    maybe?.save();\n'
        '    this.repo.find(1);\n'
        '    this.cache.find(2);\n'
        '    this.repo2.find(3);\n'
        '    util.fmt(1);\n'
        '    Order.create();\n'
        '    const fresh = new Order();\n'
        '    fresh.archive();\n'
        '    this.local();\n'
        '    [1, 2].forEach(() => this.local());\n'
        '    path.join("a", "b");\n'
        '  }\n'
        '\n'
        '  local(): void {}\n'
        '\n'
        '  viaIface(r: IRepo): void {\n'
        '    r.find(1);\n'
        '  }\n'
        '\n'
        '  arrowField = (o: Order) => {\n'
        '    o.save();\n'
        '  };\n'
        '}\n'
        '\n'
        'export function free(s: OrderService): void {\n'
        '  s.handle(new Order());\n'
        '}\n'
        '\n'
        'export function makeRepo(): SqlRepo {\n'
        '  return new SqlRepo();\n'
        '}\n'
        '\n'
        'export function viaReturn(): void {\n'
        '  const r = makeRepo();\n'
        '  r.find(1);\n'
        '  makeRepo().find(2);\n'
        '}\n'
    ),
    'src/nest/article.service.ts': (
        'import { Injectable } from "@nestjs/common";\n'
        'import { Repository } from "typeorm";\n'
        'import { Order } from "../models";\n'
        '\n'
        '@Injectable()\n'
        'export class ArticleService {\n'
        '  constructor(private readonly orders: Repository<Order>) {}\n'
        '\n'
        '  findAll(): Order[] {\n'
        '    return [];\n'
        '  }\n'
        '\n'
        '  findOne(id: number): Order {\n'
        '    const all = this.findAll();\n'
        '    this.orders.find();\n'
        '    return all[0];\n'
        '  }\n'
        '}\n'
    ),
    'src/nest/article.controller.ts': (
        'import { Controller, Get, Param } from "@nestjs/common";\n'
        'import { ArticleService } from "./article.service";\n'
        '\n'
        '@Controller("articles")\n'
        'export class ArticleController {\n'
        '  constructor(private readonly articleService: ArticleService) {}\n'
        '\n'
        '  @Get()\n'
        '  async findAll() {\n'
        '    return this.articleService.findAll();\n'
        '  }\n'
        '\n'
        '  @Get(":id")\n'
        '  async findOne(@Param("id") id: number) {\n'
        '    return this.articleService.findOne(id);\n'
        '  }\n'
        '}\n'
    ),
    'src/ng/widget.ts': (
        'import { inject } from "@angular/core";\n'
        'import { OrderService } from "../service";\n'
        '\n'
        'export class Widget {\n'
        '  private svc = inject(OrderService);\n'
        '\n'
        '  run(): void {\n'
        '    this.svc.local();\n'
        '  }\n'
        '}\n'
    ),
    'src/service.spec.ts': (
        'import { Order } from "./models";\n'
        '\n'
        'export function check(o: Order): void {\n'
        '  o.save();\n'
        '}\n'
    ),
    'packages/a/svc.ts': (
        'export class Svc {\n'
        '  run(): void {}\n'
        '}\n'
    ),
    'packages/b/svc.ts': (
        'export class Svc {\n'
        '  run(): void {}\n'
        '}\n'
    ),
    'src/ws.ts': (
        'import { Svc } from "@a/svc";\n'
        '\n'
        'export function useSvc(s: Svc): void {\n'
        '  s.run();\n'
        '}\n'
    ),
}

TS_MUSTNOT = {
    'src/m.ts': (
        'export class Repo {\n'
        '  find(): void {}\n'
        '\n'
        '  static make(): Repo {\n'
        '    return new Repo();\n'
        '  }\n'
        '}\n'
        '\n'
        'export class Cache {\n'
        '  find(): void {}\n'
        '}\n'
        '\n'
        'export interface IRepo {\n'
        '  find(): void;\n'
        '}\n'
        '\n'
        'export class SqlRepo implements IRepo {\n'
        '  find(): void {}\n'
        '}\n'
        '\n'
        'export class Order {\n'
        '  save(): void {}\n'
        '}\n'
        '\n'
        'export class Box<T> {\n'
        '  get(x: T): T {\n'
        '    return x;\n'
        '  }\n'
        '\n'
        '  peek(): void {}\n'
        '}\n'
    ),
    'src/neg.ts': (
        'import { Repo, Cache, IRepo, Order, Box } from "./m";\n'
        'import type { Order as OrderT } from "./m";\n'
        '\n'
        'export function viaInterface(r: IRepo): void {\n'
        '  r.find();\n'
        '}\n'
        '\n'
        'export function viaUnion(r: Repo | Cache): void {\n'
        '  r.find();\n'
        '}\n'
        '\n'
        'export function viaCast(x: unknown): void {\n'
        '  (x as Repo).find();\n'
        '}\n'
        '\n'
        'export function viaAny(a: any): void {\n'
        '  a.find();\n'
        '}\n'
        '\n'
        'export function viaDestructure({ repo }: { repo: Repo }): void {\n'
        '  repo.find();\n'
        '}\n'
        '\n'
        'export function viaRebind(): void {\n'
        '  let r = new Repo();\n'
        '  r = Repo.make();\n'
        '  r.find();\n'
        '}\n'
        '\n'
        'export function viaTypeOnly(o: OrderT): void {\n'
        '  o.save();\n'
        '}\n'
        '\n'
        'export function typeOnlyValue(): void {\n'
        '  new OrderT();\n'
        '}\n'
        '\n'
        'export class Holder<Order> {\n'
        '  use(x: Order): void {\n'
        '    x.save();\n'
        '  }\n'
        '}\n'
        '\n'
        'export function boxed(b: Box<Order>): void {\n'
        '  b.peek();\n'
        '}\n'
        '\n'
        'export function thisInFunction(): void {\n'
        '  [1].forEach(function () {\n'
        '    this.save();\n'
        '  });\n'
        '}\n'
        '\n'
        'export const objLit = {\n'
        '  save(): void {},\n'
        '  run(): void {\n'
        '    this.save();\n'
        '  },\n'
        '};\n'
        '\n'
        'export class Sub extends Repo {\n'
        '  run(): void {\n'
        '    this["find"]();\n'
        '    this.find.call(this);\n'
        '  }\n'
        '\n'
        '  viaFnExpr(): void {\n'
        '    [1].forEach(function () {\n'
        '      this.find();\n'
        '    });\n'
        '  }\n'
        '}\n'
        '\n'
        'export function viaInterfaceMissing(r: IRepo): void {\n'
        '  r.extra();\n'
        '}\n'
        '\n'
        'export interface Merged {\n'
        '  extra(): void;\n'
        '}\n'
        '\n'
        'export class Merged {\n'
        '  run(): void {\n'
        '    this.extra();\n'
        '  }\n'
        '}\n'
        '\n'
        'export function forOf(rs: Repo[]): void {\n'
        '  for (const r of rs) {\n'
        '    r.find();\n'
        '  }\n'
        '}\n'
        '\n'
        'export class Lone {\n'
        '  run(): void {\n'
        '    this.save();\n'
        '  }\n'
        '}\n'
    ),
    'src/view.tsx': (
        'import { Order } from "./m";\n'
        '\n'
        'export function View(): any {\n'
        '  return <Order />;\n'
        '}\n'
    ),
    'src/models.py': (
        'class Order:\n'
        '    def save(self):\n'
        '        return 1\n'
    ),
}

JS_APP = {
    'src/models/repo.js': (
        'class Repo {\n'
        '  find(id) {\n'
        '    return id;\n'
        '  }\n'
        '\n'
        '  static create() {\n'
        '    return new Repo();\n'
        '  }\n'
        '}\n'
        '\n'
        'class Cache {\n'
        '  find(id) {\n'
        '    return id;\n'
        '  }\n'
        '}\n'
        '\n'
        'module.exports = { Repo, Cache };\n'
    ),
    'src/models/order.js': (
        "const { Repo } = require('./repo');\n"
        '\n'
        'class Base {\n'
        '  helper() {\n'
        '    return 1;\n'
        '  }\n'
        '}\n'
        '\n'
        'class Order extends Base {\n'
        '  cache = new Repo();\n'
        '\n'
        '  constructor() {\n'
        '    super();\n'
        '    this.repo = new Repo();\n'
        '  }\n'
        '\n'
        '  save() {\n'
        '    this.helper();\n'
        '    this.repo.find(1);\n'
        '    this.cache.find(2);\n'
        '    return this;\n'
        '  }\n'
        '}\n'
        '\n'
        'module.exports = Order;\n'
    ),
    'src/services/user.service.js': (
        'const getUser = async (id) => ({ id });\n'
        '\n'
        'const createUser = async (body) => getUser(body.id);\n'
        '\n'
        'function helper() {\n'
        '  return 1;\n'
        '}\n'
        '\n'
        'module.exports = {\n'
        '  getUser,\n'
        '  createUser,\n'
        '};\n'
    ),
    'src/services/index.js': (
        "module.exports.userService = require('./user.service');\n"
    ),
    'src/controllers/user.controller.js': (
        "const { userService } = require('../services');\n"
        "const userSvc = require('../services/user.service');\n"
        "const Order = require('../models/order');\n"
        "const { Repo } = require('../models/repo');\n"
        '\n'
        'async function show(req) {\n'
        '  const u = await userService.getUser(req.id);\n'
        '  await userSvc.createUser(req.body);\n'
        '  const o = new Order();\n'
        '  o.save();\n'
        '  Repo.create();\n'
        '  return u;\n'
        '}\n'
        '\n'
        'class Ctl {\n'
        '  run() {\n'
        '    this.go();\n'
        '    [1].forEach(() => this.go());\n'
        '  }\n'
        '\n'
        '  go() {}\n'
        '\n'
        '  missing() {\n'
        '    this.notHere();\n'
        '  }\n'
        '}\n'
        '\n'
        'class Other {\n'
        '  notHere() {}\n'
        '}\n'
        '\n'
        'module.exports = { show, Ctl, Other };\n'
    ),
    'src/esm/u.mjs': (
        'export function f() {\n'
        '  return 1;\n'
        '}\n'
    ),
    'src/esm/use.mjs': (
        "import * as u from './u.mjs';\n"
        '\n'
        'export function callF() {\n'
        '  return u.f();\n'
        '}\n'
    ),
}

JS_MUSTNOT = {
    'src/lib/repo.js': (
        'class Repo {\n'
        '  find() {}\n'
        '}\n'
        '\n'
        'function make() {\n'
        '  return new Repo();\n'
        '}\n'
        '\n'
        'module.exports = { Repo, make };\n'
    ),
    'src/lib/h.js': (
        'function list() {\n'
        "  return 'private';\n"
        '}\n'
        '\n'
        'function other() {\n'
        "  return 'exported';\n"
        '}\n'
        '\n'
        'module.exports = { list: other };\n'
    ),
    'src/lib/dyn.js': (
        'function go() {}\n'
        '\n'
        'Object.assign(module.exports, { go });\n'
    ),
    'src/lib/inst.js': (
        'class Svc {\n'
        '  m() {}\n'
        '}\n'
        '\n'
        'module.exports = new Svc();\n'
    ),
    'src/use.js': (
        "const h = require('./lib/h');\n"
        "const dyn = require('./lib/dyn');\n"
        "const inst = require('./lib/inst');\n"
        "const { Repo, make } = require('./lib/repo');\n"
        '\n'
        '/** @param {Repo} r */\n'
        'function jsdoc(r) {\n'
        '  r.find();\n'
        '}\n'
        '\n'
        'function useH() {\n'
        '  h.list();\n'
        '}\n'
        '\n'
        'function useDyn() {\n'
        '  dyn.go();\n'
        '}\n'
        '\n'
        'function useInst() {\n'
        '  inst.m();\n'
        '}\n'
        '\n'
        'function thisInFn() {\n'
        '  [1].forEach(function () {\n'
        '    this.find();\n'
        '  });\n'
        '}\n'
        '\n'
        'const obj = {\n'
        '  find() {},\n'
        '  run() {\n'
        '    this.find();\n'
        '  },\n'
        '};\n'
        '\n'
        'function rebind() {\n'
        '  let r = new Repo();\n'
        '  r = make();\n'
        '  r.find();\n'
        '}\n'
        '\n'
        'function forOf(rs) {\n'
        '  for (const r of rs) {\n'
        '    r.find();\n'
        '  }\n'
        '}\n'
        '\n'
        'function viaString() {\n'
        "  const s = 'r.find()';\n"
        '  // r.find();\n'
        '  return `${s} find()`;\n'
        '}\n'
        '\n'
        'module.exports = { jsdoc, useH, useDyn, useInst, thisInFn, obj, rebind, forOf, viaString };\n'
    ),
}

# The second must-not pair (the S3c review round): path aliases no config maps, `export *` beside a private
# local, type-level shadowing, unchecked files and lines, computed member names, globals and builtins
# patched by the project, writes to a field through `this` aliases and callbacks, prototype mixins, binding
# forms with an outer typed name, JavaScript arity, a receiver deeper than the IR allows.
_DEEP_CHAIN = "".join(f".p{i}" for i in range(60))

TS_MUSTNOT2 = {
    'package.json': '{ "name": "app2", "dependencies": { "lodash": "^4.17.21" } }\n',
    'src/m2.ts': (
        'export class Repo {\n'
        '  find(): void {}\n'
        '\n'
        '  static make(): Repo {\n'
        '    return new Repo();\n'
        '  }\n'
        '}\n'
        '\n'
        'export class Cache {\n'
        '  find(): void {}\n'
        '}\n'
        '\n'
        'export class Other {\n'
        '  find(): void {}\n'
        '}\n'
        '\n'
        'export namespace M {\n'
        '  export class Inner {\n'
        '    find(): void {}\n'
        '  }\n'
        '}\n'
    ),
    'src/utils/format.ts': (
        'export function formatDate(d: Date): string {\n'
        '  return d.toISOString();\n'
        '}\n'
    ),
    'src/alias.ts': (
        'import { formatDate } from "@utils/format";\n'
        'import { get } from "lodash";\n'
        'import { thing } from "undeclared-pkg";\n'
        'import { Repo } from "./m2";\n'
        '\n'
        'export function render(d: Date): string {\n'
        '  return formatDate(d);\n'
        '}\n'
        '\n'
        'export function viaLodash(o: object): unknown {\n'
        '  return get(o, "a");\n'
        '}\n'
        '\n'
        'export function viaUndeclared(): unknown {\n'
        '  return thing();\n'
        '}\n'
        '\n'
        'export function viaRepo(r: Repo): void {\n'
        '  r.find();\n'
        '}\n'
    ),
    'src/other.ts': (
        'export function helper(): number {\n'
        '  return 1;\n'
        '}\n'
    ),
    'src/barrel.ts': (
        'export * from "./other";\n'
        '\n'
        'function helper(): number {\n'
        '  return 2;\n'
        '}\n'
        '\n'
        'export function run(): number {\n'
        '  return helper();\n'
        '}\n'
    ),
    'src/usebarrel.ts': (
        'import * as a from "./barrel";\n'
        'import * as o from "./other";\n'
        '\n'
        'export function main(): number {\n'
        '  return a.helper();\n'
        '}\n'
        '\n'
        'export function viaOther(): number {\n'
        '  return o.helper();\n'
        '}\n'
    ),
    'src/shadow.ts': (
        'import { Repo, Other, M } from "./m2";\n'
        '\n'
        'export function localType(get: () => Other): void {\n'
        '  type Repo = Other;\n'
        '  const r: Repo = get();\n'
        '  r.find();\n'
        '}\n'
        '\n'
        'export namespace N {\n'
        '  type Repo = Other;\n'
        '\n'
        '  export function h(r: Repo): void {\n'
        '    r.find();\n'
        '  }\n'
        '}\n'
        '\n'
        'export namespace N2 {\n'
        '  import Repo = M.Inner;\n'
        '\n'
        '  export function h2(r: Repo): void {\n'
        '    r.find();\n'
        '  }\n'
        '}\n'
        '\n'
        'export function stillRepo(r: Repo): void {\n'
        '  r.find();\n'
        '}\n'
    ),
    'src/nocheck.ts': (
        '// @ts-nocheck\n'
        'export class Loose {\n'
        '  run(): void {\n'
        '    this.extra();\n'
        '  }\n'
        '}\n'
    ),
    'src/ignore.ts': (
        'export class Ign {\n'
        '  run(): void {\n'
        '    // @ts-ignore\n'
        '    this.extra();\n'
        '    this.other();\n'
        '  }\n'
        '\n'
        '  other(): void {}\n'
        '}\n'
    ),
    'src/computed.ts': (
        'export class Comp {\n'
        '  ["save"](): void {}\n'
        '\n'
        '  run(): void {\n'
        '    this.save();\n'
        '    this.nothing();\n'
        '  }\n'
        '}\n'
        '\n'
        'export class Sym {\n'
        '  [Symbol.iterator](): any {\n'
        '    return null;\n'
        '  }\n'
        '\n'
        '  run(): void {\n'
        '    this.missing();\n'
        '  }\n'
        '}\n'
    ),
    'src/glob.ts': (
        'declare global {\n'
        '  interface Window {\n'
        '    winHelper(): number;\n'
        '  }\n'
        '}\n'
        '\n'
        'export function winHelper(): number {\n'
        '  return 1;\n'
        '}\n'
        '\n'
        'window.winHelper = winHelper;\n'
        '\n'
        'export function viaWindow(): number {\n'
        '  return window.winHelper();\n'
        '}\n'
        '\n'
        'export function viaDoc(): void {\n'
        '  document.createElement("div");\n'
        '}\n'
    ),
    'src/mutants.ts': (
        'import { Repo, Cache } from "./m2";\n'
        'import type { Repo as RT } from "./m2";\n'
        '\n'
        'export function m1(r: Repo, o: { r: Cache }): void {\n'
        '  if (o) {\n'
        '    const { r } = o;\n'
        '    r.find();\n'
        '  }\n'
        '}\n'
        '\n'
        'export function m2(e: Repo): void {\n'
        '  try {\n'
        '    e.find();\n'
        '  } catch (e) {\n'
        '    e.find();\n'
        '  }\n'
        '}\n'
        '\n'
        'export function m3(): Repo {\n'
        '  return RT.make();\n'
        '}\n'
        '\n'
        'export function m4(r: Repo, rs: Cache[]): void {\n'
        '  for (const r of rs) {\n'
        '    r.find();\n'
        '  }\n'
        '}\n'
        '\n'
        'export function m5(r: Repo): void {\n'
        '  r.find();\n'
        '}\n'
    ),
    'src/svc.ts': (
        'import { Repo, Cache } from "./m2";\n'
        '\n'
        'export class Svc {\n'
        '  repo = new Repo();\n'
        '\n'
        '  useCache(): void {\n'
        '    const self = this;\n'
        '    self.repo = new Cache();\n'
        '  }\n'
        '\n'
        '  run(): void {\n'
        '    this.repo.find();\n'
        '  }\n'
        '}\n'
        '\n'
        'export class Svc2 {\n'
        '  repo = new Repo();\n'
        '\n'
        '  later(): void {\n'
        '    setTimeout(function (this: Svc2) {\n'
        '      this.repo = new Cache();\n'
        '    }.bind(this), 1);\n'
        '  }\n'
        '\n'
        '  run(): void {\n'
        '    this.repo.find();\n'
        '  }\n'
        '}\n'
        '\n'
        'export class Keep {\n'
        '  store = new Repo();\n'
        '\n'
        '  run(): void {\n'
        '    this.store.find();\n'
        '  }\n'
        '}\n'
    ),
    'src/deep.ts': (
        'import { Repo } from "./m2";\n'
        '\n'
        'export function chain(q: any): void {\n'
        '  q' + _DEEP_CHAIN + '.m();\n'
        '}\n'
        '\n'
        'export function typedHere(r: Repo): void {\n'
        '  r.find();\n'
        '}\n'
    ),
}

JS_MUSTNOT2 = {
    'src/lib/money.js': (
        'export function toCents(x) {\n'
        '  return Math.round(x * 100);\n'
        '}\n'
    ),
    'src/lib/index.js': 'export { toCents } from "./money";\n',
    'src/helpers/fmt.js': (
        'export function fmt(x) {\n'
        '  return "" + x;\n'
        '}\n'
    ),
    'src/pay.js': (
        'import { toCents } from "$lib";\n'
        'import { fmt } from "@helpers/fmt";\n'
        'import { join } from "path";\n'
        '\n'
        'export function charge(x) {\n'
        '  return toCents(x);\n'
        '}\n'
        '\n'
        'export function show(x) {\n'
        '  return fmt(x);\n'
        '}\n'
        '\n'
        'export function viaPath(a) {\n'
        '  return join(a, "b");\n'
        '}\n'
    ),
    'src/star/other.js': (
        'export function helper() {\n'
        '  return 1;\n'
        '}\n'
    ),
    'src/star/a.js': (
        'export * from "./other";\n'
        '\n'
        'function helper() {\n'
        '  return 2;\n'
        '}\n'
        '\n'
        'export function run() {\n'
        '  return helper();\n'
        '}\n'
    ),
    'src/star/b.js': (
        'import * as a from "./a";\n'
        '\n'
        'export function main() {\n'
        '  return a.helper();\n'
        '}\n'
    ),
    'src/poly.js': (
        'String.prototype.capitalize = function capitalize() {\n'
        '  return this.toUpperCase();\n'
        '};\n'
        '\n'
        'export function titleCase(s) {\n'
        '  return String(s).capitalize();\n'
        '}\n'
        '\n'
        'export function num(x) {\n'
        '  return Number(x).toFixed(2);\n'
        '}\n'
    ),
    'src/store.js': (
        'export class Repo {\n'
        '  find() {\n'
        '    return 1;\n'
        '  }\n'
        '}\n'
        '\n'
        'export class Cache {\n'
        '  find() {\n'
        '    return 2;\n'
        '  }\n'
        '}\n'
    ),
    'src/svc_alias.js': (
        'import { Repo, Cache } from "./store";\n'
        '\n'
        'export class Svc {\n'
        '  constructor() {\n'
        '    this.repo = new Repo();\n'
        '  }\n'
        '\n'
        '  useCache() {\n'
        '    var self = this;\n'
        '    self.repo = new Cache();\n'
        '  }\n'
        '\n'
        '  run() {\n'
        '    return this.repo.find();\n'
        '  }\n'
        '}\n'
    ),
    'src/svc_bind.js': (
        'import { Repo, Cache } from "./store";\n'
        '\n'
        'export class Svc2 {\n'
        '  constructor() {\n'
        '    this.repo = new Repo();\n'
        '  }\n'
        '\n'
        '  later() {\n'
        '    setTimeout(function () {\n'
        '      this.repo = new Cache();\n'
        '    }.bind(this), 1);\n'
        '  }\n'
        '\n'
        '  run() {\n'
        '    return this.repo.find();\n'
        '  }\n'
        '}\n'
    ),
    'src/svc_write.js': (
        'import { Repo, Cache } from "./store";\n'
        '\n'
        'export class Svc3 {\n'
        '  constructor() {\n'
        '    this.repo = new Repo();\n'
        '  }\n'
        '\n'
        '  run() {\n'
        '    return this.repo.find();\n'
        '  }\n'
        '}\n'
        '\n'
        'export function swap(s) {\n'
        '  s.repo = new Cache();\n'
        '}\n'
        '\n'
        'export class Keep {\n'
        '  constructor() {\n'
        '    this.store = new Repo();\n'
        '  }\n'
        '\n'
        '  run() {\n'
        '    return this.store.find();\n'
        '  }\n'
        '}\n'
    ),
    'src/mixk.js': (
        'export class Klass {\n'
        '  find() {\n'
        '    return 1;\n'
        '  }\n'
        '\n'
        '  run() {\n'
        '    return this.find();\n'
        '  }\n'
        '}\n'
        '\n'
        'export class Other2 {\n'
        '  go() {\n'
        '    return 1;\n'
        '  }\n'
        '}\n'
    ),
    'src/mix.js': (
        'import { Klass, Other2 } from "./mixk";\n'
        '\n'
        'export const mixin = {\n'
        '  find() {\n'
        '    return 2;\n'
        '  },\n'
        '};\n'
        '\n'
        'Object.assign(Klass.prototype, mixin);\n'
        'Object.assign(Other2.prototype, {\n'
        '  go() {\n'
        '    return 3;\n'
        '  },\n'
        '});\n'
    ),
    'src/usek.js': (
        'import { Klass, Other2 } from "./mixk";\n'
        '\n'
        'export function u() {\n'
        '  const k = new Klass();\n'
        '  return k.find();\n'
        '}\n'
        '\n'
        'export function v() {\n'
        '  const o = new Other2();\n'
        '  return o.go();\n'
        '}\n'
    ),
    'src/arity.js': (
        'export class Repo2 {\n'
        '  find(id, opts) {\n'
        '    return id;\n'
        '  }\n'
        '\n'
        '  list() {\n'
        '    return arguments.length;\n'
        '  }\n'
        '}\n'
        '\n'
        'export function one() {\n'
        '  const r = new Repo2();\n'
        '  return r.find(1);\n'
        '}\n'
        '\n'
        'export function many() {\n'
        '  const r = new Repo2();\n'
        '  return r.list(1, 2, 3);\n'
        '}\n'
    ),
}

# Dynamic dispatch (the core keeps a project OVERRIDE reachable). graphify binds a member call to the LAST same-named
# definition of its file (both engines), so here every `.m()` of shapes.ts is bound to Sub.m, of sub2.ts to Sub2.m and
# (JS_DISPATCH) of jsd.js to JSub.m: for a declared receiver (`r: Base`) or `this` that pick IS an override reachable
# at run time (in-file and cross-file subclasses, TS and JS) and stays, with its own grade; for an unrelated receiver
# (`r: Leaf`) and a constructed one (`new Base()`: exactly that class) it is false and goes.
TS_DISPATCH = {
    "src/shapes.ts": (
        'export class Leaf {\n'
        '  m(): number {\n'
        '    return 3;\n'
        '  }\n'
        '}\n'
        '\n'
        'export function onLeaf(r: Leaf): number {\n'
        '  return r.m();\n'
        '}\n'
        '\n'
        'export class Base {\n'
        '  m(): number {\n'
        '    return 1;\n'
        '  }\n'
        '\n'
        '  run(): number {\n'
        '    return this.m();\n'
        '  }\n'
        '}\n'
        '\n'
        'export function use(r: Base): number {\n'
        '  return r.m();\n'
        '}\n'
        '\n'
        'export function made(): number {\n'
        '  const b = new Base();\n'
        '  return b.m();\n'
        '}\n'
        '\n'
        'export class Sub extends Base {\n'
        '  m(): number {\n'
        '    return 2;\n'
        '  }\n'
        '}\n'
    ),
    "src/sub2.ts": (
        'import { Base } from "./shapes";\n'
        '\n'
        'export class Sub2 extends Base {\n'
        '  m(): number {\n'
        '    return 4;\n'
        '  }\n'
        '}\n'
        '\n'
        'export function viaSub2(r: Base): number {\n'
        '  return r.m();\n'
        '}\n'
    ),
}

JS_DISPATCH = {
    "src/jsd.js": (
        'export class JBase {\n'
        '  m() {\n'
        '    return 1;\n'
        '  }\n'
        '\n'
        '  run() {\n'
        '    return this.m();\n'
        '  }\n'
        '}\n'
        '\n'
        'export function jmade() {\n'
        '  const b = new JBase();\n'
        '  return b.m();\n'
        '}\n'
        '\n'
        'export class JSub extends JBase {\n'
        '  m() {\n'
        '    return 2;\n'
        '  }\n'
        '}\n'
    ),
}

# A builtin prototype the project patches (the second verification's probe, on the real engine): the engine's tuple of
# a literal / builtin-typed receiver's member call reaches the project function the prototype holds, so it stays
# (typed_calls.PATCH_RULES "loose"); an unpatched builtin member call is still external.
TS_GLOBALS = {
    "src/poly.js": (
        'function capitalize() {\n'
        '  return this.charAt(0).toUpperCase() + this.slice(1);\n'
        '}\n'
        'String.prototype.capitalize = capitalize;\n'
        '\n'
        'export function greet(name) {\n'
        "  return 'x'.capitalize() + name;\n"
        '}\n'
    ),
    "src/poly2.ts": (
        'declare global {\n'
        '  interface String {\n'
        '    shout(): string;\n'
        '  }\n'
        '}\n'
        '\n'
        'export function shout(this: string): string {\n'
        '  return this.toUpperCase();\n'
        '}\n'
        '\n'
        'String.prototype.shout = shout;\n'
        '\n'
        'export function loud(s: string): string {\n'
        '  return s.shout();\n'
        '}\n'
        '\n'
        'export function loud2(): string {\n'
        "  return 'abc'.shout();\n"
        '}\n'
    ),
}

# The phase-2 review: a write through a type assertion (`(x as any) = v`, `(<any>x) = v`, `(this as any).f = v`) still
# writes x / this.f — it must poison x (an untyped field binding for this.f), never leave the declared type standing
TS_CASTS = {
    "src/casts.ts": (
        'export class TRepo {\n'
        '  find(): number {\n'
        '    return 1;\n'
        '  }\n'
        '}\n'
        '\n'
        'export class TFake {\n'
        '  find(): number {\n'
        '    return 2;\n'
        '  }\n'
        '}\n'
        '\n'
        'let tcur = new TRepo();\n'
        '\n'
        'export function tswap(): void {\n'
        '  (tcur as any) = new TFake();\n'
        '}\n'
        '\n'
        'export function tuse(): number {\n'
        '  return tcur.find();\n'
        '}\n'
        '\n'
        'let tcur2 = new TRepo();\n'
        '\n'
        'export function tswap2(): void {\n'
        '  (<any>tcur2) = new TFake();\n'
        '}\n'
        '\n'
        'export function tuse2(): number {\n'
        '  return tcur2.find();\n'
        '}\n'
        '\n'
        'const tok = new TRepo();\n'
        '\n'
        'export function tokUse(): number {\n'
        '  return tok.find();\n'
        '}\n'
        '\n'
        'export class Late {\n'
        '  handler: TRepo = new TRepo();\n'
        '\n'
        '  setup(): void {\n'
        '    (this as any).handler = new TFake();\n'
        '  }\n'
        '\n'
        '  go(): number {\n'
        '    return this.handler.find();\n'
        '  }\n'
        '}\n'
        '\n'
        'export class Early {\n'
        '  handler: TRepo = new TRepo();\n'
        '\n'
        '  go(): number {\n'
        '    return this.handler.find();\n'
        '  }\n'
        '}\n'
    ),
}

# The phase-2 review: graphify's symbol-resolution pass walks the WHOLE body of a root `function f() {}` / `const f =
# () => {}` and gives it every bare call of an imported name — nested arrows / declarations included. The core reads
# such a nested site as UNCERTAIN: the engine's tuple stays (no REPLACE), nothing is emitted from it. A class method
# is never walked: its nested arrow's typed site is detached, so an injected name tuple of it is REPLACEd.
JS_NEST = {
    "src/foo.js": (
        'export class Foo {\n'
        '  run() {\n'
        '    return 1;\n'
        '  }\n'
        '}\n'
    ),
    "src/nest.js": (
        "import { Foo } from './foo';\n"
        '\n'
        'function outer() {\n'
        '  const inner = () => Foo();\n'
        '  return inner;\n'
        '}\n'
        '\n'
        'const outer2 = () => {\n'
        '  function inner2() {\n'
        '    return Foo();\n'
        '  }\n'
        '  return inner2;\n'
        '};\n'
        '\n'
        'export class K {\n'
        '  m() {\n'
        '    const f = () => Foo();\n'
        '    return f;\n'
        '  }\n'
        '}\n'
        '\n'
        'export { outer, outer2 };\n'
    ),
}

TREES = {"ts_app": TS_APP, "ts_mustnot": TS_MUSTNOT, "js_app": JS_APP, "js_mustnot": JS_MUSTNOT,
         "ts_mustnot2": TS_MUSTNOT2, "js_mustnot2": JS_MUSTNOT2, "ts_dispatch": TS_DISPATCH,
         "js_dispatch": JS_DISPATCH, "ts_globals": TS_GLOBALS, "ts_casts": TS_CASTS, "js_nest": JS_NEST}

# A unit tree for the binder (never extracted: section D builds the BinderContext by hand).
BINDER = {
    "tsconfig.json": '{ "compilerOptions": { "baseUrl": ".", "paths": { "@x/*": ["src/x/*"] } } }\n',
    "src/x/util.ts": "export function f(): void {}\nexport const g = 1;\n",
    "src/esm/a.ts": "export class A {\n  m(): void {}\n}\n",
    "src/esm/b.ts": "export class A {\n  m(): void {}\n}\n",
    "src/esm/c.ts": "export function onlyC(): void {}\n",
    "src/esm/stars.ts": 'export * from "./a";\nexport * from "./b";\nexport * from "./c";\n',
    "src/esm/stars2.ts": 'export * from "./c";\nexport * from "lodash";\n',
    "src/esm/ren.ts": "function f(): void {}\nfunction g(): void {}\nexport { g as f };\n",
    "src/esm/ren2.ts": "function g(): void {}\nexport { g as h };\n",
    "src/esm/def.ts": "export default class D {\n  m(): void {}\n}\n",
    "src/esm/defexpr.ts": "export default {\n  m() {},\n};\n",
    "src/esm/nsre.ts": 'export * as util from "../x/util";\n',
    "src/esm/node.ts": "export function nn(): void {}\n",
    "src/esm/hidden.ts": "export function secret(): void {}\n",
    "src/cjs/whole.js": "class W {}\nmodule.exports = W;\n",
    "src/cjs/obj.js": "function a() {}\nmodule.exports = { a };\n",
    "src/cjs/props.js": "exports.p = function () {};\nexports.q = q;\nfunction q() {}\n",
    "src/cjs/barrel.js": "module.exports.obj = require('./obj');\nmodule.exports.whole = require('./whole');\n",
    "src/cjs/ren.js": "function list() {}\nfunction other() {}\nmodule.exports = { list: other };\n",
    "src/cjs/dyn.js": "const k = 'x';\nexports[k] = 1;\n",
    "src/cjs/inst.js": "class S {}\nmodule.exports = new S();\n",
    "src/cjs/reexp.js": "module.exports = require('./obj');\n",
    "packages/pk/package.json": '{ "name": "@ws/pk" }\n',
    "packages/pk/src/index.ts": "export class P {}\n",
    "src/use.ts": "export {};\n",
    "package.json": '{ "dependencies": { "lodash": "^4.17.21" }, "devDependencies": { "@acme/ui": "1.0.0" } }\n',
    "src/esm/starlocal.ts": 'export * from "./c";\nfunction onlyC(): void {}\nexport function own(): void {}\n',
    "src/esm/starclean.ts": 'export * from "./c";\nexport function own(): void {}\n',
    "src/glob.ts": "window.binderGlobal = 1;\nexport {};\n",
}
BINDER_NON_GRAPH = {"src/esm/hidden.ts"}

ON = {"DEEPMAP_TYPED_CALLS": "typescript,javascript"}

# The expected result of every tree on BOTH captures ("file::name" ids; grades from the caller's call_typed).
EXPECT = {
    "ts_app": {
        "typed": {
            ("src/models/order.ts::Order.save", "src/models/base.ts::Base.helper"): "INFERRED",
            ("src/models/order.ts::Order.create", "src/models/order.ts::Order"): "EXTRACTED",
            ("src/models/order.ts::Order.archive", "src/models/base.ts::Base.helper"): "INFERRED",
            ("src/models/repo.ts::SqlRepo.find", "src/models/order.ts::Order"): "INFERRED",
            ("src/nest/article.controller.ts::ArticleController.findAll",
             "src/nest/article.service.ts::ArticleService.findAll"): "INFERRED",
            ("src/nest/article.controller.ts::ArticleController.findOne",
             "src/nest/article.service.ts::ArticleService.findOne"): "INFERRED",
            ("src/nest/article.service.ts::ArticleService.findOne",
             "src/nest/article.service.ts::ArticleService.findAll"): "EXTRACTED",
            ("src/ng/widget.ts::Widget.run", "src/service.ts::OrderService.local"): "INFERRED",
            ("src/schemas.ts::ZodNumber.multipleOf", "src/schemas.ts::ZodNumber._addCheck"): "EXTRACTED",
            ("src/schemas.ts::ZodDate.min", "src/schemas.ts::ZodDate._addCheck"): "EXTRACTED",
            ("src/service.ts::OrderService.constructor", "src/models/repo.ts::SqlRepo"): "INFERRED",
            ("src/service.ts::OrderService.handle", "src/models/order.ts::Order"): "INFERRED",
            ("src/service.ts::OrderService.handle", "src/models/order.ts::Order.archive"): "INFERRED",
            ("src/service.ts::OrderService.handle", "src/models/order.ts::Order.create"): "INFERRED",
            ("src/service.ts::OrderService.handle", "src/models/repo.ts::Cache.find"): "INFERRED",
            ("src/service.ts::OrderService.handle", "src/models/repo.ts::SqlRepo.find"): "INFERRED",
            ("src/service.ts::OrderService.handle", "src/lib/util.ts::fmt"): "INFERRED",
            ("src/service.ts::OrderService.handle", "src/service.ts::OrderService.local"): "EXTRACTED",
            ("src/service.ts::OrderService.handle", "src/models/order.ts::Order.save"): "INFERRED",
            ("src/service.ts::OrderService.arrowField", "src/models/order.ts::Order.save"): "INFERRED",
            ("src/service.ts::free", "src/models/order.ts::Order"): "INFERRED",
            ("src/service.ts::free", "src/service.ts::OrderService.handle"): "EXTRACTED",
            ("src/ws.ts::useSvc", "packages/a/svc.ts::Svc.run"): "INFERRED",
            ("src/models/order.ts::Order.clone", "src/models/order.ts::Order.create"): "EXTRACTED",  # static `this`
            ("src/service.ts::makeRepo", "src/models/repo.ts::SqlRepo"): "INFERRED",
            ("src/service.ts::viaReturn", "src/models/repo.ts::SqlRepo.find"): "INFERRED",   # a return annotation
        },
        # what each engine linked, and what the typed pass does to it
        "engine_removed": {
            ("src/nest/article.service.ts::ArticleService.findOne", "ArticleService.findAll"),
            ("src/schemas.ts::ZodNumber.multipleOf", "ZodDate._addCheck"),       # the zod in-file last-def bind
            ("src/schemas.ts::ZodDate.min", "ZodDate._addCheck"),
            ("src/schemas.ts::ZodDate.stray", "ZodNumber.multipleOf"),           # absent in .ts: removed, no edge
            ("src/service.ts::OrderService.handle", "OrderService.local"),
            ("src/service.ts::free", "OrderService.handle"),
            ("src/models/order.ts::Order.clone", "Order.create"),
        },
        "engine_kept": set(),
        "inject": [("src/service.ts::OrderService.handle", "save"), ("src/service.ts::OrderService.handle", "find"),
                   ("src/service.ts::OrderService.handle", "join"), ("src/service.ts::OrderService.viaIface", "find"),
                   ("src/nest/article.service.ts::ArticleService.findOne", "find"),
                   ("src/schemas.ts::ZodDate.stray", "multipleOf"), ("src/service.spec.ts::check", "save")],
        "inject_removed": {("src/service.ts::OrderService.handle", "save"),
                           ("src/service.ts::OrderService.handle", "find"),
                           ("src/service.ts::OrderService.handle", "join"),     # path.join: external
                           ("src/schemas.ts::ZodDate.stray", "multipleOf"),
                           ("src/service.spec.ts::check", "save")},              # a test caller's CORRECTION
        "inject_kept": {("src/service.ts::OrderService.viaIface", "find"),     # an interface: no node
                        ("src/nest/article.service.ts::ArticleService.findOne", "find")},  # Repository<T>
        "inject_typed": {("src/service.spec.ts::check", "src/models/order.ts::Order.save"): "INFERRED"},
        "mustnot": ["src/service.ts::OrderService.viaIface", "src/service.spec.ts::check",
                    "src/schemas.ts::ZodDate.stray"],
        "skipped_policy": 1,
    },
    "ts_mustnot": {
        "typed": {
            ("src/m.ts::Repo.make", "src/m.ts::Repo"): "EXTRACTED",
            ("src/neg.ts::viaRebind", "src/m.ts::Repo"): "INFERRED",
            ("src/neg.ts::viaRebind", "src/m.ts::Repo.make"): "INFERRED",
            ("src/neg.ts::viaTypeOnly", "src/m.ts::Order.save"): "INFERRED",           # an `import type` in a type
            ("src/neg.ts::boxed", "src/m.ts::Box.peek"): "INFERRED",                   # Box<Order>: Box, never Order
        },
        "engine_removed": set(),
        "engine_kept": set(),
        "inject": [("src/neg.ts::viaInterface", "find"), ("src/neg.ts::viaUnion", "find"),
                   ("src/neg.ts::viaCast", "find"), ("src/neg.ts::viaAny", "find"),
                   ("src/neg.ts::viaDestructure", "find"), ("src/neg.ts::viaRebind", "find"),
                   ("src/neg.ts::typeOnlyValue", "OrderT"), ("src/neg.ts::Holder.use", "save"),
                   ("src/neg.ts::thisInFunction", "save"), ("src/neg.ts::Sub.run", "find"),
                   ("src/neg.ts::Sub.viaFnExpr", "find"), ("src/neg.ts::viaInterfaceMissing", "extra"),
                   ("src/neg.ts::Merged.run", "extra"), ("src/neg.ts::forOf", "find"),
                   ("src/neg.ts::Lone.run", "save"), ("src/neg.ts::boxed", "peek"),
                   ("src/neg.ts::viaTypeOnly", "save"), ("src/view.tsx::View", "Order")],
        "inject_removed": {("src/neg.ts::Lone.run", "save"),               # absent in a closed .ts class
                           ("src/neg.ts::boxed", "peek"), ("src/neg.ts::viaTypeOnly", "save")},
        "inject_kept": {("src/neg.ts::viaInterface", "find"), ("src/neg.ts::viaUnion", "find"),
                        ("src/neg.ts::viaCast", "find"), ("src/neg.ts::viaAny", "find"),
                        ("src/neg.ts::viaDestructure", "find"), ("src/neg.ts::viaRebind", "find"),
                        ("src/neg.ts::typeOnlyValue", "OrderT"), ("src/neg.ts::Holder.use", "save"),
                        ("src/neg.ts::thisInFunction", "save"), ("src/neg.ts::Sub.run", "find"),
                        ("src/neg.ts::Sub.viaFnExpr", "find"), ("src/neg.ts::viaInterfaceMissing", "extra"),
                        ("src/neg.ts::Merged.run", "extra"), ("src/neg.ts::forOf", "find"),
                        ("src/view.tsx::View", "Order")},                  # JSX is no call
        "inject_typed": {},
        "mustnot": ["src/neg.ts::viaInterface", "src/neg.ts::viaUnion", "src/neg.ts::viaCast", "src/neg.ts::viaAny",
                    "src/neg.ts::viaDestructure", "src/neg.ts::typeOnlyValue", "src/neg.ts::Holder.use",
                    "src/neg.ts::thisInFunction", "src/neg.ts::Sub.run", "src/neg.ts::Sub.viaFnExpr",
                    "src/neg.ts::viaInterfaceMissing", "src/neg.ts::Merged.run", "src/neg.ts::forOf",
                    "src/neg.ts::Lone.run", "src/view.tsx::View"],
        "skipped_policy": 0,
    },
    "js_app": {
        "typed": {
            ("src/controllers/user.controller.js::show", "src/models/order.js::Order"): "INFERRED",
            ("src/controllers/user.controller.js::show", "src/models/repo.js::Repo.create"): "INFERRED",
            ("src/controllers/user.controller.js::show", "src/services/user.service.js::createUser"): "INFERRED",
            ("src/controllers/user.controller.js::show", "src/services/user.service.js::getUser"): "INFERRED",
            ("src/controllers/user.controller.js::show", "src/models/order.js::Order.save"): "INFERRED",
            ("src/controllers/user.controller.js::Ctl.run", "src/controllers/user.controller.js::Ctl.go"):
                "EXTRACTED",
            ("src/esm/use.mjs::callF", "src/esm/u.mjs::f"): "INFERRED",
            ("src/models/order.js::Order.constructor", "src/models/repo.js::Repo"): "INFERRED",
            ("src/models/order.js::Order.save", "src/models/repo.js::Repo.find"): "INFERRED",
            ("src/models/order.js::Order.save", "src/models/order.js::Base.helper"): "EXTRACTED",
            ("src/models/repo.js::Repo.create", "src/models/repo.js::Repo"): "EXTRACTED",
        },
        "engine_removed": {("src/controllers/user.controller.js::Ctl.run", "Ctl.go"),
                           ("src/models/order.js::Order.save", "Base.helper")},
        "engine_kept": {("src/controllers/user.controller.js::Ctl.missing", "Other.notHere"),   # .js: never absent
                        ("src/services/user.service.js::createUser", "getUser")},              # a bare call
        "inject": [("src/controllers/user.controller.js::show", "save"),
                   ("src/controllers/user.controller.js::Ctl.missing", "notHere")],
        "inject_removed": {("src/controllers/user.controller.js::show", "save")},
        "inject_kept": {("src/controllers/user.controller.js::Ctl.missing", "notHere")},
        "inject_typed": {},
        "mustnot": ["src/controllers/user.controller.js::Ctl.missing"],
        "skipped_policy": 0,
    },
    "js_mustnot": {
        "typed": {
            ("src/lib/repo.js::make", "src/lib/repo.js::Repo"): "EXTRACTED",
            ("src/use.js::rebind", "src/lib/repo.js::Repo"): "INFERRED",
        },
        "engine_removed": set(),
        "engine_kept": {("src/use.js::rebind", "make")},
        "inject": [("src/use.js::jsdoc", "find"), ("src/use.js::useH", "list"), ("src/use.js::useDyn", "go"),
                   ("src/use.js::useInst", "m"), ("src/use.js::thisInFn", "find"), ("src/use.js::rebind", "find"),
                   ("src/use.js::forOf", "find"), ("src/use.js::viaString", "find")],
        "inject_removed": set(),
        "inject_kept": {("src/use.js::jsdoc", "find"), ("src/use.js::useH", "list"), ("src/use.js::useDyn", "go"),
                        ("src/use.js::useInst", "m"), ("src/use.js::thisInFn", "find"),
                        ("src/use.js::rebind", "find"), ("src/use.js::forOf", "find"),
                        ("src/use.js::viaString", "find")},
        "inject_typed": {},
        "mustnot": ["src/use.js::jsdoc", "src/use.js::useH", "src/use.js::useDyn", "src/use.js::useInst",
                    "src/use.js::thisInFn", "src/use.js::forOf", "src/use.js::viaString"],
        "skipped_policy": 0,
    },
    "ts_mustnot2": {
        "typed": {
            ("src/alias.ts::viaRepo", "src/m2.ts::Repo.find"): "INFERRED",
            ("src/ignore.ts::Ign.run", "src/ignore.ts::Ign.other"): "EXTRACTED",      # the line after @ts-ignore only
            ("src/m2.ts::Repo.make", "src/m2.ts::Repo"): "EXTRACTED",
            ("src/mutants.ts::m5", "src/m2.ts::Repo.find"): "INFERRED",
            ("src/shadow.ts::stillRepo", "src/m2.ts::Repo.find"): "INFERRED",       # the outer Repo is still Repo
            ("src/svc.ts::Keep.run", "src/m2.ts::Repo.find"): "INFERRED",
            ("src/svc.ts::Svc.useCache", "src/m2.ts::Cache"): "INFERRED",
            ("src/svc.ts::Svc2.later", "src/m2.ts::Cache"): "INFERRED",              # the callback's `new Cache()`
            ("src/usebarrel.ts::viaOther", "src/other.ts::helper"): "INFERRED",      # a plain module
            ("src/deep.ts::typedHere", "src/m2.ts::Repo.find"): "INFERRED",          # the file beside a deep chain
        },
        "engine_removed": {("src/ignore.ts::Ign.run", "Ign.other")},
        "engine_kept": {("src/alias.ts::render", "formatDate"),                     # `@utils/format`: unknown
                        ("src/glob.ts::viaWindow", "winHelper"),                    # window.winHelper: patched
                        ("src/barrel.ts::run", "helper")},                          # a bare call
        "inject": [("src/alias.ts::viaLodash", "get"), ("src/alias.ts::viaUndeclared", "thing"),
                   ("src/usebarrel.ts::main", "helper"), ("src/usebarrel.ts::viaOther", "helper"),
                   ("src/shadow.ts::localType", "find"), ("src/shadow.ts::h", "find"), ("src/shadow.ts::h2", "find"),
                   ("src/shadow.ts::stillRepo", "find"), ("src/nocheck.ts::Loose.run", "extra"),
                   ("src/ignore.ts::Ign.run", "extra"), ("src/computed.ts::Comp.run", "save"),
                   ("src/computed.ts::Comp.run", "nothing"),
                   ("src/computed.ts::Sym.run", "missing"), ("src/glob.ts::viaDoc", "createElement"),
                   ("src/mutants.ts::m1", "find"), ("src/mutants.ts::m2", "find"), ("src/mutants.ts::m3", "make"),
                   ("src/mutants.ts::m4", "find"), ("src/mutants.ts::m5", "find"), ("src/svc.ts::Svc.run", "find"),
                   ("src/svc.ts::Svc2.run", "find"), ("src/svc.ts::Keep.run", "find"),
                   ("src/deep.ts::typedHere", "find"), ("src/deep.ts::chain", "m")],
        "inject_removed": {("src/alias.ts::viaLodash", "get"),                     # a declared dependency: external
                           ("src/usebarrel.ts::viaOther", "helper"), ("src/shadow.ts::stillRepo", "find"),
                           ("src/glob.ts::viaDoc", "createElement"),               # an unpatched builtin: external
                           ("src/computed.ts::Comp.run", "nothing"),               # absent: Comp stays closed
                           ("src/mutants.ts::m5", "find"), ("src/svc.ts::Keep.run", "find"),
                           ("src/deep.ts::typedHere", "find")},
        "inject_kept": {("src/alias.ts::viaUndeclared", "thing"),                  # no package.json declares it
                        ("src/usebarrel.ts::main", "helper"),                      # `export *` beside a private local
                        ("src/shadow.ts::localType", "find"), ("src/shadow.ts::h", "find"),
                        ("src/shadow.ts::h2", "find"),                             # local type / import aliases
                        ("src/nocheck.ts::Loose.run", "extra"),                    # @ts-nocheck: never absent
                        ("src/ignore.ts::Ign.run", "extra"),                       # @ts-ignore line: never absent
                        ("src/computed.ts::Comp.run", "save"),                     # ["save"]() is declared
                        ("src/computed.ts::Sym.run", "missing"),                   # a computed member opens the class
                        ("src/mutants.ts::m1", "find"), ("src/mutants.ts::m2", "find"),
                        ("src/mutants.ts::m3", "make"), ("src/mutants.ts::m4", "find"),
                        ("src/svc.ts::Svc.run", "find"),                           # `self.repo = new Cache()`
                        ("src/svc.ts::Svc2.run", "find"),                          # `function () {...}.bind(this)`
                        ("src/deep.ts::chain", "m")},
        "inject_typed": {},
        "mustnot": ["src/shadow.ts::localType", "src/shadow.ts::h", "src/shadow.ts::h2", "src/usebarrel.ts::main",
                    "src/nocheck.ts::Loose.run", "src/computed.ts::Comp.run", "src/computed.ts::Sym.run",
                    "src/glob.ts::viaWindow", "src/mutants.ts::m1", "src/mutants.ts::m2", "src/mutants.ts::m3",
                    "src/mutants.ts::m4", "src/svc.ts::Svc.run", "src/svc.ts::Svc2.run", "src/alias.ts::render",
                    "src/alias.ts::viaUndeclared", "src/deep.ts::chain"],
        "skipped_policy": 0,
    },
    "js_mustnot2": {
        "typed": {
            ("src/arity.js::one", "src/arity.js::Repo2"): "EXTRACTED",
            ("src/arity.js::one", "src/arity.js::Repo2.find"): "EXTRACTED",          # JavaScript has no arity rule
            ("src/arity.js::many", "src/arity.js::Repo2"): "EXTRACTED",
            ("src/arity.js::many", "src/arity.js::Repo2.list"): "EXTRACTED",
            ("src/svc_alias.js::Svc.constructor", "src/store.js::Repo"): "INFERRED",
            ("src/svc_alias.js::Svc.useCache", "src/store.js::Cache"): "INFERRED",
            ("src/svc_bind.js::Svc2.constructor", "src/store.js::Repo"): "INFERRED",
            ("src/svc_bind.js::Svc2.later", "src/store.js::Cache"): "INFERRED",
            ("src/svc_write.js::Keep.constructor", "src/store.js::Repo"): "INFERRED",
            ("src/svc_write.js::Keep.run", "src/store.js::Repo.find"): "INFERRED",
            ("src/svc_write.js::Svc3.constructor", "src/store.js::Repo"): "INFERRED",
            ("src/svc_write.js::swap", "src/store.js::Cache"): "INFERRED",
            ("src/usek.js::u", "src/mixk.js::Klass"): "INFERRED",
            ("src/usek.js::v", "src/mixk.js::Other2"): "INFERRED",
        },
        "engine_removed": {("src/arity.js::one", "Repo2.find")},
        "engine_kept": {("src/mixk.js::Klass.run", "Klass.find"),                   # the mixin replaces find
                        ("src/pay.js::charge", "toCents"),                          # `$lib`: never a package
                        ("src/pay.js::show", "fmt"),                                # `@helpers/fmt`: a project dir
                        ("src/star/a.js::run", "helper")},
        "inject": [("src/pay.js::viaPath", "join"), ("src/star/b.js::main", "helper"),
                   ("src/poly.js::titleCase", "capitalize"), ("src/poly.js::num", "toFixed"),
                   ("src/svc_alias.js::Svc.run", "find"), ("src/svc_bind.js::Svc2.run", "find"),
                   ("src/svc_write.js::Svc3.run", "find"), ("src/svc_write.js::Keep.run", "find"),
                   ("src/mixk.js::Klass.run", "find"), ("src/usek.js::u", "find"), ("src/usek.js::v", "go"),
                   ("src/arity.js::many", "list")],
        "inject_removed": {("src/pay.js::viaPath", "join"),                        # a Node builtin: external
                           ("src/poly.js::num", "toFixed"),                        # an unpatched builtin: external
                           ("src/svc_write.js::Keep.run", "find"), ("src/arity.js::many", "list")},
        "inject_kept": {("src/star/b.js::main", "helper"),
                        ("src/poly.js::titleCase", "capitalize"),                  # String.prototype is patched
                        ("src/svc_alias.js::Svc.run", "find"), ("src/svc_bind.js::Svc2.run", "find"),
                        ("src/svc_write.js::Svc3.run", "find"),                    # `s.repo = new Cache()` elsewhere
                        ("src/mixk.js::Klass.run", "find"), ("src/usek.js::u", "find"),
                        ("src/usek.js::v", "go")},                                 # Object.assign(Other2.prototype, {go})
        "inject_typed": {},
        "mustnot": ["src/star/b.js::main", "src/svc_alias.js::Svc.run", "src/svc_bind.js::Svc2.run",
                    "src/svc_write.js::Svc3.run", "src/mixk.js::Klass.run", "src/pay.js::charge", "src/pay.js::show",
                    "src/poly.js::titleCase"],
        "skipped_policy": 0,
    },
    "ts_dispatch": {
        "typed": {
            ("src/shapes.ts::onLeaf", "src/shapes.ts::Leaf.m"): "EXTRACTED",
            ("src/shapes.ts::Base.run", "src/shapes.ts::Base.m"): "EXTRACTED",
            ("src/shapes.ts::use", "src/shapes.ts::Base.m"): "EXTRACTED",
            ("src/shapes.ts::made", "src/shapes.ts::Base"): "EXTRACTED",
            ("src/shapes.ts::made", "src/shapes.ts::Base.m"): "EXTRACTED",
            ("src/sub2.ts::viaSub2", "src/shapes.ts::Base.m"): "INFERRED",
        },
        # the engine's in-file pick: an unrelated class's method / a constructed receiver's subclass -> removed
        "engine_removed": {("src/shapes.ts::onLeaf", "Sub.m"), ("src/shapes.ts::made", "Sub.m")},
        # ...an override a declared receiver or `this` reaches (in-file / cross-file) -> kept whole, its own grade
        "engine_kept": {("src/shapes.ts::use", "Sub.m"), ("src/shapes.ts::Base.run", "Sub.m"),
                        ("src/sub2.ts::viaSub2", "Sub2.m")},
        # an engine name fan-out (every `m`): its unrelated targets go, its overrides stay reachable — Sub.m through
        # the kept `Sub.m` tuple, Sub2.m re-emitted as a key at AMBIGUOUS (a fan-out's grade, never an upgrade)
        "inject": [("src/shapes.ts::use", "m"), ("src/shapes.ts::onLeaf", "m"), ("src/shapes.ts::made", "m"),
                   ("src/sub2.ts::viaSub2", "m")],
        "inject_removed": {("src/shapes.ts::use", "m"), ("src/shapes.ts::onLeaf", "m"), ("src/shapes.ts::made", "m"),
                           ("src/sub2.ts::viaSub2", "m")},
        "inject_kept": set(),
        "inject_typed": {("src/shapes.ts::use", "src/sub2.ts::Sub2.m"): "AMBIGUOUS",
                         ("src/sub2.ts::viaSub2", "src/shapes.ts::Sub.m"): "AMBIGUOUS"},
        "inject_rekeyed": {("src/shapes.ts::use", "src/sub2.ts::Sub2.m"),
                           ("src/sub2.ts::viaSub2", "src/shapes.ts::Sub.m")},
        "mustnot": [],
        "skipped_policy": 0,
    },
    "js_dispatch": {
        "typed": {
            ("src/jsd.js::JBase.run", "src/jsd.js::JBase.m"): "EXTRACTED",
            ("src/jsd.js::jmade", "src/jsd.js::JBase"): "EXTRACTED",
            ("src/jsd.js::jmade", "src/jsd.js::JBase.m"): "EXTRACTED",
        },
        "engine_removed": {("src/jsd.js::jmade", "JSub.m")},                       # constructed: exactly JBase
        "engine_kept": {("src/jsd.js::JBase.run", "JSub.m")},                      # `this` may be a JSub
        # an `m` fan-out that reaches only the static target and the override is kept whole (its own grade)
        "inject": [("src/jsd.js::JBase.run", "m")],
        "inject_removed": set(),
        "inject_kept": {("src/jsd.js::JBase.run", "m")},
        "inject_typed": {},
        "mustnot": [],
        "skipped_policy": 0,
    },
    "ts_globals": {
        "typed": {},
        "engine_removed": set(),
        # a literal / string-typed receiver's member call of a member the project patches onto String.prototype
        # (`declare global { interface String { shout(): string } }` too): the engine's tuple to the project function
        # stays (PATCH_RULES loose: never an external member call)
        "engine_kept": {("src/poly.js::greet", "capitalize"), ("src/poly2.ts::loud", "shout"),
                        ("src/poly2.ts::loud2", "shout")},
        "inject": [],
        "inject_removed": set(),
        "inject_kept": set(),
        "inject_typed": {},
        "mustnot": ["src/poly.js::greet", "src/poly2.ts::loud", "src/poly2.ts::loud2"],
        "skipped_policy": 0,
    },
    "ts_casts": {
        "typed": {
            ("src/casts.ts::tswap", "src/casts.ts::TFake"): "EXTRACTED",
            ("src/casts.ts::tswap2", "src/casts.ts::TFake"): "EXTRACTED",
            ("src/casts.ts::tokUse", "src/casts.ts::TRepo.find"): "EXTRACTED",
            ("src/casts.ts::Late.setup", "src/casts.ts::TFake"): "EXTRACTED",
            ("src/casts.ts::Early.go", "src/casts.ts::TRepo.find"): "EXTRACTED",
        },
        # the engine's in-file pick `TFake.find`: FALSE behind a never-rebound `tok` / a field bound once -> removed
        "engine_removed": {("src/casts.ts::tokUse", "TFake.find"), ("src/casts.ts::Early.go", "TFake.find")},
        # `(tcur as any) = new TFake()` / `(<any>tcur2) = ...` rebind the module-level local; `(this as any).handler =
        # new TFake()` rebinds the field: the receiver is unknown and the engine's (TRUE) `TFake.find` tuple stays
        "engine_kept": {("src/casts.ts::tuse", "TFake.find"), ("src/casts.ts::tuse2", "TFake.find"),
                        ("src/casts.ts::Late.go", "TFake.find")},
        "inject": [("src/casts.ts::tuse", "find"), ("src/casts.ts::Late.go", "find"),
                   ("src/casts.ts::tokUse", "find"), ("src/casts.ts::Early.go", "find")],
        "inject_removed": {("src/casts.ts::tokUse", "find"), ("src/casts.ts::Early.go", "find")},
        "inject_kept": {("src/casts.ts::tuse", "find"), ("src/casts.ts::Late.go", "find")},
        "inject_typed": {},
        "mustnot": ["src/casts.ts::tuse", "src/casts.ts::tuse2", "src/casts.ts::Late.go"],
        "skipped_policy": 0,
    },
    "js_nest": {
        "typed": {},
        "engine_removed": set(),
        # graphify's symbol pass gave these top-level functions the bare `Foo()` of their nested arrow / declaration:
        # the core reads that site as uncertain — the tuple stays, nothing is emitted from it
        "engine_kept": {("src/nest.js::outer", "Foo"), ("src/nest.js::outer2", "Foo")},
        # a class method is never walked: its nested arrow's typed `Foo()` is detached, so a name tuple is REPLACEd
        "inject": [("src/nest.js::K.m", "Foo")],
        "inject_removed": {("src/nest.js::K.m", "Foo")},
        "inject_kept": set(),
        "inject_typed": {},
        "mustnot": ["src/nest.js::outer", "src/nest.js::outer2", "src/nest.js::K.m"],
        "skipped_policy": 0,
    },
}
# never a typed key to these (the private `list` beside `module.exports = { list: other }`; the private `helper`s
# beside an `export *`; methods a prototype mixin replaces)
NEVER_TARGETS = {"src/lib/h.js::list", "src/barrel.ts::helper", "src/star/a.js::helper", "src/mixk.js::Klass.find",
                 "src/mixk.js::Other2.go"}


# ================================================================================================
# Helpers
# ================================================================================================

_TMP: list = []


def tmp() -> Path:
    if not _TMP:
        _TMP.append(Path(tempfile.mkdtemp(prefix="dm_s3c_")))
    return _TMP[0]


def cleanup() -> None:
    if _TMP:
        shutil.rmtree(_TMP.pop(), ignore_errors=True)


def tree_sha(name: str) -> str:
    return hashlib.sha256(json.dumps(TREES[name], sort_keys=True).encode("utf-8")).hexdigest()


def write_tree(root: Path, files: dict) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    return root


_ROOTS: dict = {}


def root_for(name: str, files=None) -> Path:
    """Tree `name` materialized once under the gate's temp dir."""
    if name not in _ROOTS:
        _ROOTS[name] = write_tree(tmp() / "src" / name, files if files is not None else TREES[name])
    return _ROOTS[name]


def fixture_path(bucket: str, name: str) -> Path:
    return FX / bucket / "ts" / f"{name}.json"


_CAPS: dict = {}


def load_capture(bucket: str, name: str):
    """A capture, read once per run (never mutated: pipeline() maps a copy of its graph)."""
    p = fixture_path(bucket, name)
    if not p.is_file():
        return None
    key = (str(p), p.stat().st_mtime_ns)
    if key not in _CAPS:
        _CAPS[key] = json.loads(p.read_text(encoding="utf-8"))
    return _CAPS[key]


@contextlib.contextmanager
def clean_env(**kv):
    saved = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def node_ids(nodes) -> dict:
    """key -> "file::name" (a name two nodes share gets "@line" on both)."""
    base: dict = {}
    for n in nodes:
        base.setdefault(n["key"], (f'{n.get("source_file")}::{n.get("name")}', n.get("start_line")))
    count: dict = {}
    for v, _ln in base.values():
        count[v] = count.get(v, 0) + 1
    return {k: (v if count[v] == 1 else f"{v}@{ln}") for k, (v, ln) in base.items()}


class Run:
    """One pipeline run: map_graph -> _apply_recognizers -> _unkey_receiver_calls -> typed_calls.link."""

    def __init__(self, nodes, inp, before, out, stats):
        self.nodes, self.inp, self.before, self.out, self.stats = nodes, inp, before, out, stats
        self.id_of = node_ids(nodes)
        self.key_of = {v: k for k, v in self.id_of.items()}
        self.node = {n["key"]: n for n in nodes}

    def typed(self) -> dict:
        out = {}
        for c in self.out:
            if GX.is_call_key(c[1]):
                g = (self.node.get(c[0], {}).get("call_typed") or {}).get(c[1])
                out[(self.id_of.get(c[0], c[0]), self.id_of.get(c[1], c[1]))] = g
        return out

    def pairs(self, calls) -> list:
        return [(self.id_of.get(c[0], c[0]), str(c[1]), bool(GX.is_call_key(c[1]))) for c in calls]

    def removed(self) -> set:
        after = self.pairs(self.out)
        gone = []
        for p in self.pairs(self.before):
            if p in after:
                after.remove(p)
            else:
                gone.append(p)
        return {(a, b) for a, b, k in gone if not k}

    def has_name_tuple(self, caller_id, name) -> bool:
        return (caller_id, name, False) in self.pairs(self.out)


_PREP: dict = {}


def prepared(graph, root) -> tuple:
    """(nodes, calls) of `graph` after map_graph -> _apply_recognizers -> _unkey_receiver_calls (every DEEPMAP_
    flag cleared): the reader plays no part in it, so it is computed once per graph object and handed out as a
    fresh deep copy (typed_calls.link sets node attributes)."""
    key = (id(graph), str(root))
    got = _PREP.get(key)
    if got is None or got[0] is not graph:
        with clean_env(**{k: None for k in [k for k in os.environ if k.startswith("DEEPMAP_")]}):
            nodes, calls, _imports = GX.map_graph(json.loads(json.dumps(graph)), root=str(root))
            nodes, _typed = S._apply_recognizers(str(root), nodes)
            calls, _n = S._unkey_receiver_calls(str(root), nodes, calls)
        got = (graph, nodes, list(calls))
        _PREP[key] = got
    return copy.deepcopy(got[1]), list(got[2])


def pipeline(graph, root, env, inject=(), mod=None, typed_env=None) -> Run:
    """`graph` (a graph.json) of `root` through the sync's passes into typed_calls.link under `env`; `inject`
    appends (caller id, name) tuples as an engine name fan-out would; `mod` = a (mutant) reader module."""
    nodes, calls = prepared(graph, root)
    ids = node_ids(nodes)
    key_of = {v: k for k, v in ids.items()}
    for cid, name in inject:
        calls.append((key_of[cid], name))
    before = list(calls)
    readers = None
    if mod is not None:
        readers = {lang: mod.make_reader(TC.LANGUAGES[lang]) for lang in ("typescript", "javascript")}
    out, st = TC.link(str(root), nodes, calls, env=env, readers=readers, is_test=S._is_test_path)
    return Run(nodes, calls, before, out, st)


def captured_runs(env=ON, inject=False, mod=None, trees=None) -> dict:
    """{(bucket, tree): Run} over every capture the repository holds."""
    out = {}
    for b in BUCKETS.values():
        for name in (trees or TREES):
            cap = load_capture(b, name)
            if cap is None:
                continue
            out[(b, name)] = pipeline(cap["graph"], root_for(name), env,
                                      EXPECT[name]["inject"] if inject else (), mod=mod)
    return out


def facts_of(name: str, mod=None) -> dict:
    """{rel: FileFacts} of every TS / JS file of a tree, through the reader and the sync's shared reader."""
    m = mod or R
    root = root_for(name, TREES.get(name) or BINDER)
    src = GX._Source(root)
    files = TREES.get(name) or BINDER
    out = {}
    for rel in sorted(files):
        lang = "typescript" if rel.lower().endswith((".ts", ".tsx", ".mts", ".cts")) else (
            "javascript" if rel.lower().endswith((".js", ".jsx", ".mjs", ".cjs")) else None)
        if lang:
            out[rel] = m.make_reader(TC.LANGUAGES[lang]).facts(rel, src)
    return out


def scope(ff, name, cls=None):
    """The one scope named `name` (of class `cls` when given), else None."""
    idx = None if cls is None else {i for i, c in enumerate(ff.classes) if c.name == cls and c.kind != "interface"}
    got = [s for s in ff.scopes if s.name == name and (idx is None or s.cls in idx)]
    return got[0] if len(got) == 1 else None


def snippet(text: str, rel: str = "snip.ts", mod=None):
    m = mod or R
    data = text.encode("utf-8")
    lang = "javascript" if rel.lower().endswith((".js", ".jsx", ".mjs", ".cjs")) else "typescript"
    return m.read_facts(rel, lang, m.parse_bytes(data, m.dialect_of(rel)), data)


# ================================================================================================
# A: the module contract
# ================================================================================================

def sec_contract(rec: Rec, mod=None) -> None:
    src = (BUILD / "receiver_facts_ts.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    std = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}
    top = []
    for st in tree.body:
        if isinstance(st, ast.Import):
            top += [a.name.split(".")[0] for a in st.names]
        elif isinstance(st, ast.ImportFrom):
            top.append((st.module or "").split(".")[0])
    rec.check("A module-level imports of receiver_facts_ts are stdlib only (typed_calls, tree-sitter, "
              "typescript_edges are lazy)", top and all(m in std for m in top), [m for m in top if m not in std])
    try:
        ast.parse(src, feature_version=(3, 10))
        ok310 = True
    except SyntaxError as exc:
        ok310 = repr(exc)
    rec.check("A the reader is Python 3.10 syntax", ok310 is True, ok310)
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import receiver_facts_ts; "
            "print(sorted(m for m in ('graphify_extract', 'edge_resolve', 'sync', 'typescript_edges', 'tree_sitter', "
            "'typed_calls', 'language_recognizers', 'receiver_facts_py') if m in sys.modules))")
    p = subprocess.run([sys.executable, "-I", "-c", code, str(BUILD)], capture_output=True, text=True, timeout=120)
    rec.check("A importing receiver_facts_ts loads no sibling / engine module", p.returncode == 0
              and p.stdout.strip() == "[]", (p.returncode, p.stdout, p.stderr[-400:]))
    rec.check("A the registry hands typescript AND javascript to receiver_facts_ts",
              TC.LANGUAGES["typescript"].reader == "receiver_facts_ts"
              and TC.LANGUAGES["javascript"].reader == "receiver_facts_ts")
    rd = R.make_reader(TC.LANGUAGES["typescript"])
    rec.check("A make_reader gives a Reader (version, canary, facts, binder)",
              isinstance(rd.version, str) and rd.version and all(callable(getattr(rd, a, None))
                                                                 for a in ("canary", "facts", "binder")))
    # typescript_edges.modules_for: an additive twin of the resolver finalize uses
    te_src = (BUILD / "typescript_edges.py").read_text(encoding="utf-8")
    te_tree = ast.parse(te_src)
    callers = sorted({fn.name for fn in ast.walk(te_tree) if isinstance(fn, ast.FunctionDef)
                      for x in ast.walk(fn) if isinstance(x, ast.Call) and isinstance(x.func, ast.Name)
                      and x.func.id == "modules_for"})
    rec.check("A typescript_edges.modules_for exists and nothing in typescript_edges calls it (recognize / "
              "finalize unchanged)", hasattr(TE, "modules_for") and callers == [], callers)
    root = root_for("ts_app")
    files = {r for r in TREES["ts_app"] if r.endswith(".ts")}
    a = TE.modules_for(root, files)
    b = TE._Modules(root, set(files), lambda rel: None)
    specs = [("src/service.ts", s) for s in ("./models", "./models/repo", "@lib/util", "path", "@a/svc", "./nope",
                                              "@/missing", "node:fs", "@nestjs/common", "../x")]
    rec.check("A modules_for(...).resolve == _Modules(...).resolve over relative, tsconfig-paths, package and missing "
              "specifiers", all(a.resolve(r, s) == b.resolve(r, s) for r, s in specs),
              [(s, a.resolve(r, s), b.resolve(r, s)) for r, s in specs])


# ================================================================================================
# B: the grammar canary
# ================================================================================================

def sec_canary(rec: Rec, mod=None) -> None:
    for lang in ("typescript", "javascript"):
        rec.check(f"B {lang}: the canary passes on this machine's grammar",
                  R.make_reader(TC.LANGUAGES[lang]).canary() is True)
    saved = dict(R._LANGS)
    real = R._language

    def broken(dialect):
        raise ImportError("no grammar")

    try:
        R._LANGS.clear()
        R._language = broken
        rec.check("B a grammar that cannot load -> canary False (never an exception)",
                  R.make_reader(TC.LANGUAGES["typescript"]).canary() is False
                  and R.make_reader(TC.LANGUAGES["javascript"]).canary() is False)
    finally:
        R._language = real
        R._LANGS.clear()
        R._LANGS.update(saved)

    class Broken:
        version = "x"

        def canary(self):
            return False

        def facts(self, rel, source):
            raise AssertionError("never read")

        def binder(self, ctx):
            return None

    cap = load_capture("0839", "js_app") or load_capture("0851", "js_app")
    if cap is None:
        rec.check("B a js_app capture exists", False)
        return
    root = root_for("js_app")
    with clean_env(**{k: None for k in [k for k in os.environ if k.startswith("DEEPMAP_")]}):
        nodes, calls, _i = GX.map_graph(json.loads(json.dumps(cap["graph"])), root=str(root))
        nodes, _t = S._apply_recognizers(str(root), nodes)
    out, st = TC.link(str(root), nodes, calls, env=ON, is_test=S._is_test_path,
                      readers={"typescript": Broken(), "javascript": R.make_reader(TC.LANGUAGES["javascript"])})
    typed = [c for c in out if GX.is_call_key(c[1])]
    rec.check("B a failing TypeScript canary disables typescript alone (javascript still links)",
              st["langs_disabled"] == {"typescript": "canary"} and len(typed) == len(EXPECT["js_app"]["typed"])
              and not st["error"], (st["langs_disabled"], len(typed), st["error"]))


# ================================================================================================
# C: the IR the reader writes
# ================================================================================================

def sec_facts(rec: Rec, mod=None) -> None:
    m = mod or R
    T = TC

    def snip(text, rel="snip.ts"):
        return snippet(text, rel, mod)

    every = {}
    for name in TREES:
        every[name] = facts_of(name, mod)
    bad = [(t, rel, T.validate_facts(ff) if ff is not None else "None") for t, fs in every.items()
           for rel, ff in fs.items() if ff is None or T.validate_facts(ff)]
    rec.check(f"C every TS / JS file of the {len(TREES)} trees reads to a valid FileFacts "
              f"({sum(len(v) for v in every.values())} files)", not bad, bad)
    again = facts_of("ts_app", mod)
    rec.check("C facts are deterministic (a second read of ts_app is identical)",
              all(repr(again[r]) == repr(every["ts_app"][r]) for r in again))

    svc = every["ts_app"]["src/service.ts"]
    cls = {c.name: c for c in svc.classes}
    os_ = cls.get("OrderService")
    rec.check("C fields: `cache = new Cache()` ctor, `repo2: SqlRepo` + `this.repo2 = new SqlRepo()`, the "
              "constructor parameter property `private readonly repo: SqlRepo` (an annotation)",
              os_ is not None
              and [(b.kind, b.type) for b in os_.fields.get("cache", ())] == [("ctor", T.t_name("Cache"))]
              and sorted(b.kind for b in os_.fields.get("repo2", ())) == ["ann", "ctor"]
              and [(b.kind, b.type) for b in os_.fields.get("repo", ())] == [("ann", T.t_name("SqlRepo"))],
              os_.fields if os_ else None)
    rec.check("C a function-valued field is a METHOD (`arrowField = (o) => ...`), never a field",
              os_ is not None and "arrowField" in os_.methods and "arrowField" not in os_.fields)
    h = scope(svc, "handle")
    rec.check("C params: `o: Order`, `maybe?: Order`, `n: Order | null = null` (a nullable union)",
              h is not None and h.params["o"].type == T.t_name("Order") and h.params["maybe"].type == T.t_name("Order")
              and h.params["n"].type == T.t_union(T.t_name("Order"), T.t_builtin("null")),
              h.params if h else None)
    sites = [(x.kind, x.member, x.recv.kind if x.recv else None) for x in (h.sites if h else ())]
    want = [("member", "save", "IDENT"), ("member", "save", "IDENT"), ("member", "find", "FIELD"),
            ("member", "find", "FIELD"), ("member", "find", "FIELD"), ("member", "fmt", "IDENT"),
            ("member", "create", "IDENT"), ("ctor", "Order", "CLASSREF"), ("member", "archive", "IDENT"),
            ("member", "local", "SELF"), ("member", "forEach", "LITERAL"), ("member", "join", "IDENT")]
    rec.check("C handle()'s own sites, in order (`maybe?.save()` included; the arrow's `this.local()` is the "
              "arrow's own)", sites == want, sites)
    arrows = [s for s in svc.scopes if s.kind == "arrow" and s.parent == svc.scopes.index(h)] if h else []
    rec.check("C an arrow nested in a method sees the method's `this` (cls, self_kind 'inst', a SELF site)",
              len(arrows) == 1 and arrows[0].cls == h.cls and arrows[0].self_kind == "inst"
              and [(x.member, x.recv) for x in arrows[0].sites] == [("local", T.R_SELF)])
    rec.check("C the census counts nested scopes (handle: `local` 2 = its own + the arrow's)",
              h is not None and h.all_names.get("local") == 2 and h.all_names.get("find") == 3)
    neg = every["ts_mustnot"]["src/neg.ts"]
    fe = scope(neg, "viaFnExpr")
    inner = [s for s in neg.scopes if fe is not None and s.parent == neg.scopes.index(fe)]
    rec.check("C `this` in a `function` expression inside a method is OTHER (no class)",
              len(inner) == 1 and inner[0].cls == -1 and inner[0].self_kind == ""
              and [x.recv for x in inner[0].sites] == [T.R_OTHER])
    ol = [s for s in neg.scopes if s.name == "run" and s.cls == -1]
    rec.check("C `this` in an object-literal method is OTHER", len(ol) == 1
              and [x.recv for x in ol[0].sites] == [T.R_OTHER])
    hu = scope(neg, "use")
    rec.check("C a class type parameter named like a class (`Holder<Order>`) types nothing: `x: Order` is "
              "unknown and the scope carries the parameter", hu is not None and hu.params["x"].type == T.T_UNKNOWN
              and "Order" in hu.type_params)
    tv = scope(neg, "typeOnlyValue")
    to = scope(neg, "viaTypeOnly")
    rec.check("C an `import type` name: a ctor of it has no class reference; as an annotation it is a name",
              tv is not None and [(x.kind, x.recv) for x in tv.sites] == [("ctor", None)]
              and to is not None and to.params["o"].type == T.t_name("OrderT")
              and "OrderT" in neg.imports and "OrderT" in [i.local for v in neg.imports.values() for i in v])
    rec.check("C poisoning: a reassigned local (`let r = new Repo(); r = ...`), a for-of variable, a "
              "destructured parameter", "r" in scope(neg, "viaRebind").poisoned and "r" in scope(neg, "forOf").poisoned
              and "repo" in scope(neg, "viaDestructure").poisoned)
    rec.check("C `(x as Repo).find()` and `this['find']()` are no typed receiver; `this.find.call(this)` is a "
              "`call` site on FIELD(SELF, find)",
              [x.recv for x in scope(neg, "viaCast").sites] == [T.R_OTHER]
              and [(x.member, x.recv) for x in scope(neg, "run", "Sub").sites if x.member == "call"] ==
              [("call", T.r_field(T.R_SELF, "find"))]
              and not [x for x in scope(neg, "run", "Sub").sites if x.member == "find"])
    ncls = {c.name: c for c in neg.classes if c.kind == "class"}
    ifs = {c.name: c for c in neg.classes if c.kind == "interface"}
    rec.check("C interfaces are open classes (methods abstract); a class with a same-named interface is open "
              "(declaration merging); a plain class is closed",
              "Merged" in ifs and ifs["Merged"].open and "Merged" in ncls and ncls["Merged"].open
              and not ncls["Sub"].open and "find" not in ncls["Sub"].methods)
    rec.check("C Holder / Box keep their type parameters", ncls["Holder"].type_params == frozenset({"Order"}))
    nsm = snip("export class Foo {\n  static a(): void {}\n}\nexport namespace Foo {\n  export function x(): void {}\n}\n"
               "export class Bar {}\n")
    rec.check("C a namespace merged into a class (static members added) opens it, like an interface",
              [(c.name, c.open) for c in nsm.classes] == [("Foo", True), ("Bar", False)])
    ctl = every["ts_app"]["src/nest/article.controller.ts"]
    fa = scope(ctl, "findAll")
    rec.check("C a decorated method: the node line is the name line, the decorator lines ride along, and the "
              "decorator's call is no site", fa is not None and fa.line == 9 and 8 in fa.lines
              and not any(x.member in ("Get", "Controller", "Param") for s in ctl.scopes for x in s.sites)
              and ctl.classes[0].methods["findAll"][0].lines == (8,))
    rec.check("C decls hold every method / function name (the textual veto skips them)",
              {(9, 8), (14, 8)} <= set(ctl.decls), sorted(ctl.decls))
    wid = every["ts_app"]["src/ng/widget.ts"]
    rec.check("C Angular `svc = inject(OrderService)` (inject from @angular/core) is a ctor binding",
              [(b.kind, b.type) for b in wid.classes[0].fields["svc"]] == [("ctor", T.t_name("OrderService"))])
    other_inject = snip('import { inject } from "./di";\nclass W {\n  s = inject(Svc);\n}\n')
    rec.check("C ...but `inject` from anywhere else is a plain call binding",
              [b.kind for b in other_inject.classes[0].fields["s"]] == ["call"])
    # the export tables
    mi = {rel: m.mod_info(ff) for rel, ff in every["ts_app"].items()}
    idx = mi["src/models/index.ts"]
    rec.check("C ESM export table: `export *`, `export { a } from`, `export type { T } from`",
              idx.stars == ["./order"] and idx.esm.get("SqlRepo") == ("reexport", "./repo", "SqlRepo")
              and idx.esm.get("IRepo") == ("reexport", "./repo", "IRepo"), (idx.stars, idx.esm))
    js = {rel: m.mod_info(ff) for rel, ff in every["js_app"].items()}
    rec.check("C CommonJS: `module.exports = { a, b }`, `module.exports = X`, `module.exports.k = require()`",
              js["src/services/user.service.js"].cjs_whole == (
                  "object", {"getUser": ("local", "getUser"), "createUser": ("local", "createUser")})
              and js["src/models/order.js"].cjs_whole == ("ident", "Order")
              and js["src/services/index.js"].cjs_props == {"userService": ("require", "./user.service")})
    jn = {rel: m.mod_info(ff) for rel, ff in every["js_mustnot"].items()}
    rec.check("C CommonJS: a rename (`{ list: other }`), Object.assign(module.exports) is dynamic, "
              "`module.exports = new Svc()` is a value",
              jn["src/lib/h.js"].cjs_whole == ("object", {"list": ("local", "other")})
              and jn["src/lib/dyn.js"].dynamic and jn["src/lib/inst.js"].cjs_whole == ("other",))
    ctlj = every["js_app"]["src/controllers/user.controller.js"]
    rec.check("C CommonJS imports: destructured require (named), require (module)",
              [(i.kind, i.spec, i.name) for i in ctlj.imports["userService"]] == [("named", "../services", "userService")]
              and [i.kind for i in ctlj.imports["userSvc"]] == ["module"])
    oj = every["js_app"]["src/models/order.js"]
    oc = [c for c in oj.classes if c.name == "Order"][0]
    rec.check("C JS: a class field `cache = new Repo()` and `this.repo = new Repo()` in the constructor",
              [b.kind for b in oc.fields["cache"]] == ["ctor"] and [b.kind for b in oc.fields["repo"]] == ["ctor"]
              and oc.bases == (T.t_name("Base"),))
    # the census parse-error rule, patches, dynamic / open classes, arity, .mts, .d.ts
    broken = snip("export function f(o: Order) {\n  o.save();\n  o.save(;\n  [1].forEach(() => o.save());\n}\n"
                  "export function g(o: Order) {\n  o.save();\n}\n"
                  "export class K {\n  m(o: Order): void {\n    o.save(;\n  }\n}\n")
    sb = scope(broken, "f")
    inner = [s for s in broken.scopes if sb is not None and s.parent == broken.scopes.index(sb)]
    rec.check("C a parse error inside a scope: no site there and +1 on every name of its census (no edge, no "
              "REPLACE)", sb is not None and not sb.sites and sb.all_names.get("save", 0) == 4,
              (sb.sites, sb.all_names) if sb else None)
    rec.check("C ...a scope nested in a broken one keeps no site either (recovery may have moved code into it)",
              len(inner) == 1 and not inner[0].sites, [x.sites for x in inner])
    rec.check("C ...a clean sibling keeps its sites; a class whose span holds an error is dynamic",
              [x.member for x in scope(broken, "g").sites] == ["save"]
              and [c.dynamic for c in broken.classes] == [True])
    ind = snip('export function f(o: Order) {\n  o.save();\n  o["save"]();\n  o.save.call(o);\n}\n')
    si_ = scope(ind, "f")
    rec.check("C `x[\"m\"]()` and `x.m.call()` count in the census of m without a site (m is never REPLACEd)",
              si_ is not None and si_.all_names.get("save") == 3 and [x.member for x in si_.sites] == ["save", "call"],
              (si_.all_names, [x.member for x in si_.sites]) if si_ else None)
    var = snip("export interface X<out T> {\n  m(): T;\n}\nexport function f(o: Order) {\n  o.save();\n}\n")
    rec.check("C a variance annotation (`<out T>`, an error for this grammar) inside a declaration stays local: "
              "the export table holds, the file binds normally, f keeps its site",
              not m.mod_info(var).dynamic and m.mod_info(var).esm.get("X") == ("local", "X")
              and "*" not in var.imports and [x.member for x in scope(var, "f").sites] == ["save"])
    bimp = snip('import { Order, from "./m";\nexport function f(o: Order) {\n  o.save();\n}\n')
    rec.check("C a broken import statement: a star marker (no unbound name resolves, not even to a builtin)",
              "*" in bimp.imports and bimp.imports["*"][0].kind == "star")
    bexp = snip("function g(): void {}\nexport { g as ;\n")
    rec.check("C a broken export statement: the export table is unknown", m.mod_info(bexp).dynamic)
    src = ("export class Repo {\n  get(): void {}\n}\ntype Map = Repo;\nexport function f(m: Map): void {\n  m.get();\n}\n"
           "export function g(r: Repo): void {\n  r.get();\n}\n")
    out, st, typed = mini_link({"src/a.ts": src}, [("Class", "Repo", 1), ("Method", "Repo.get", 2),
                                                    ("Function", "f", 5), ("Function", "g", 8)],
                               [("f", "get"), ("g", "get")], mod)
    rec.check("C a type alias named like a builtin (`type Map = Repo`) shadows it: `m.get()` is no external call "
              "(f's `get` tuple stays, no key), while g's `r: Repo` is typed and replaces its tuple",
              ("f", "get") in out and ("g", "get") not in out and typed == {("g", "Repo.get")} and not st["error"],
              (out, typed, st["error"]))
    ok = snip("export function f(o: Order) {\n  o.save();\n  o.save();\n}\n")
    so = scope(ok, "f")
    rec.check("C ...and without the error the census equals the sites", so is not None and so.all_names == {"save": 2})
    pat = snip("class Foo {\n  m(): void {}\n}\nFoo.prototype.bar = function () {};\nFoo.baz = 1;\n"
                  "Object.defineProperty(Foo.prototype, 'qux', {});\nclass Mix {}\n"
                  "Object.assign(Mix.prototype, {});\n")
    rec.check("C patches: `C.prototype.m =`, module-level `C.m =`, defineProperty(C.prototype, 'm'); "
              "Object.assign(C.prototype, ...) makes C dynamic",
              sorted((p[0].name, p[1]) for p in pat.patches) == [("Foo", "bar"), ("Foo", "baz"), ("Foo", "qux")]
              and [c.dynamic for c in pat.classes] == [False, True], (pat.patches, [c.dynamic for c in pat.classes]))
    dyn = snip("class A {\n  constructor() {\n    return {};\n  }\n}\nclass B {\n  constructor(x: any) {\n"
                  "    Object.assign(this, x);\n  }\n}\nclass C {\n  m(k: string): void {\n    this[k] = 1;\n  }\n}\n"
                  "class D {\n  [k: string]: any;\n}\n")
    rec.check("C a constructor returning a value or Object.assign(this) -> dynamic; `this[k] =` or an index "
              "signature -> open", [(c.dynamic, c.open) for c in dyn.classes] ==
              [(True, False), (True, False), (False, True), (False, True)], [(c.dynamic, c.open) for c in dyn.classes])
    ar = snip("function f(this: X, a: number, b?: number, c = 1, ...d: number[]): void {}\n"
                 "class K {\n  m(a: number): void {}\n  get p(): number { return 1; }\n  static s(): void {}\n}\n")
    k = ar.classes[0]
    rec.check("C arity skips a `this:` parameter; optional / default / rest; accessors are 'property', statics "
              "'static'", ar.functions["f"][0].arity == (1, -1) and k.methods["m"][0].arity == (1, 1)
              and "property" in k.methods["p"][0].flags and "static" in k.methods["s"][0].flags)
    mts = facts_of_text({"src/a.mts": "export class A {\n  m(): void {}\n}\n",
                         "src/t.d.ts": "export declare class T {\n  m(): void;\n}\n"}, mod)
    rec.check("C .mts is read with the TypeScript grammar; a .d.ts class is open",
              mts.get("src/a.mts") is not None and mts["src/a.mts"].classes[0].name == "A"
              and mts.get("src/t.d.ts") is not None and mts["src/t.d.ts"].classes[0].open)
    sec_facts_review(rec, mod)


def _rdepth(r) -> int:
    """The nesting depth of a Receiver (what typed_calls._ok_recv bounds)."""
    d = 0
    while r is not None and r.inner is not None:
        r, d = r.inner, d + 1
    return d


def sec_facts_review(rec: Rec, mod=None) -> None:
    """The facts behind the S3c review round's fixes (each shape's end-to-end effect is in ts_mustnot2 /
    js_mustnot2)."""
    T = TC

    def snip(text, rel="snip.ts"):
        return snippet(text, rel, mod)

    # receivers / types deeper than the IR's validator allows are cut (OTHER / unknown), never a dropped file
    chain = ("export function f(q: any): void {\n  q" + "".join(f".p{i}" for i in range(80)) + ".m();\n  q"
             + "".join(f".c{i}()" for i in range(80)) + ";\n  const v = q"
             + "".join(f".a{i}().b{i}" for i in range(45)) + ";\n  v.m();\n}\n"
             + "export function g(a: " + "A<" * 70 + "B" + ">" * 70 + ", u: "
             + " | ".join(f"U{i}" for i in range(70)) + "): void {\n  a.m();\n}\n")
    deep = snip(chain)
    sf = scope(deep, "f")
    depths = [_rdepth(x.recv) for s in deep.scopes for x in s.sites if x.recv is not None]
    depths += [_rdepth(b.value) for s in deep.scopes for bs in s.locals.values() for b in bs if b.value is not None]
    rec.check("C receivers and types deeper than the IR allows are cut, never invalid: the facts validate, no "
              "receiver nests deeper than 32, every call keeps its site",
              not T.validate_facts(deep) and depths and max(depths) <= 32 and sf is not None
              and sum(1 for x in sf.sites if x.member == "m") == 2 and len(sf.sites) == 2 + 80 + 45,
              (T.validate_facts(deep), max(depths) if depths else None, len(sf.sites) if sf else None))
    short = snip("export function f(q: any): void {\n  q" + "".join(f".p{i}" for i in range(25)) + ".m();\n}\n")
    rec.check("C ...a 25-link chain keeps its full structure (FIELD x25 over IDENT q)",
              [_rdepth(x.recv) for x in scope(short, "f").sites] == [25])
    # type-level declarations shadow an outer class of that name in their scope
    sh = snip("import { Repo, M } from './m';\nexport function f(): void {\n  type Repo = Other;\n  enum Kind {\n"
              "    A,\n  }\n  interface Face {}\n}\nexport namespace N {\n  import Alias = M.Inner;\n"
              "  export type Repo = X;\n  export enum E {\n    B,\n  }\n}\nexport function g(r: Repo): void {}\n")
    nsc = [s for s in sh.scopes if s.kind == "namespace"]
    rec.check("C a function- or namespace-local `type X =`, `enum X`, `interface X` and `import X = N.Y` poison X in "
              "that scope (an outer class X is never read there); the file level is untouched",
              {"Repo", "Kind", "Face"} <= set(scope(sh, "f").poisoned) and len(nsc) == 1
              and {"Alias", "Repo", "E"} <= set(nsc[0].poisoned) and "Repo" not in scope(sh, "g").poisoned
              and scope(sh, "g").params["r"].type == T.t_name("Repo"),
              (scope(sh, "f").poisoned, nsc[0].poisoned if nsc else None))
    # Scope.kind names the syntax (the core's ATTRIBUTION: graphify's call walk enters function expressions and
    # generator declarations, stops at function declarations, arrows and methods)
    kd = snip("export function f(): void {\n  function g(): void {}\n  function* h(): any {}\n  const e = function () {};\n"
              "  const ge = function* () {};\n  const a = () => 1;\n  const o = { m(): void {}, get p(): number { return 1; } };\n"
              "}\nexport class K {\n  constructor() {}\n  m(): void {}\n  f = () => 1;\n  g = function () {};\n}\n")
    kinds = sorted((s.name, s.kind) for s in kd.scopes)
    want_kinds = sorted([("f", "function_decl"), ("g", "function_decl"), ("h", "generator_decl"),
                         ("e", "function_expr"), ("ge", "function_expr"), ("a", "arrow"), ("m", "method_def"),
                         ("p", "method_def"), ("constructor", "method_def"), ("m", "method_def"), ("f", "arrow"),
                         ("g", "function_expr")])
    rec.check("C Scope.kind is the core's JS / TS vocabulary (function_decl, generator_decl, function_expr, arrow, "
              "method_def), class field values by their syntax", kinds == want_kinds and all(
                  k in T.SCOPE_KINDS for _n, k in kinds), kinds)
    # computed member names
    cm = snip("export class A {\n  ['save'](): void {}\n  'quoted'(): void {}\n}\nexport class B {\n"
              "  [Symbol.iterator](): any {\n    return null;\n  }\n}\nexport abstract class D {\n"
              "  abstract ['go'](): void;\n  abstract [Symbol.iterator](): any;\n}\nexport class F {\n"
              "  get [Symbol.toStringTag](): string {\n    return '';\n  }\n}\n")
    cls = {c.name: c for c in cm.classes}
    rec.check("C a string-literal computed name is a method (`['save']()`), any other computed member opens the "
              "class (`[Symbol.iterator]()`, an abstract one, a computed accessor)",
              {"save", "quoted"} <= set(cls["A"].methods) and not cls["A"].open and cls["B"].open
              and "go" in cls["D"].methods and cls["D"].open and cls["F"].open,
              {n: (sorted(c.methods), c.open) for n, c in cls.items()})
    # what the project patches onto globals, builtins and prototypes
    pt = facts_of_text({
        "src/g.ts": ("declare global {\n  interface Window {\n    w1(): void;\n  }\n  interface String {\n"
                     "    cap(): string;\n  }\n  var g1: number;\n  function g2(): void;\n}\n"
                     "export function init(): void {\n  window.w2 = 1;\n  globalThis.w3 = 2;\n"
                     "  Object.defineProperty(window, 'w4', {});\n}\nexport {};\n"),
        "src/m.js": ("import { K, R, S, B } from './k';\nimport { mk } from './mk';\nconst mixin = { a() {}, b: 1 };\n"
                     "mixin.c = function () {};\nObject.assign(K.prototype, mixin, { d() {} });\n"
                     "Object.assign(K.prototype, mk());\n_.extend(K.prototype, { e() {} });\n"
                     "Object.keys(R.prototype);\nconsole.log(R.prototype);\nObject.getOwnPropertyNames(R.prototype);\n"
                     "Object.defineProperties(K.prototype, { g: { value: 1 } });\n"
                     "Object.setPrototypeOf(S.prototype, B.prototype);\n"
                     "function setup(L) {\n  L.prototype = { h() {} };\n}\n"
                     "jest.spyOn(T.prototype, 'find');\nsinon.stub(U.prototype);\n"),
    }, mod)
    gp = {(p[0].name, p[1]) for p in pt["src/g.ts"].patches}
    want_g = {("window", "w1"), ("globalThis", "w1"), ("String", "cap"), ("window", "g1"), ("globalThis", "g1"),
              ("window", "g2"), ("globalThis", "g2"), ("window", "w2"), ("globalThis", "w3"), ("window", "w4")}
    rec.check("C patches: `declare global` members (Window -> window / globalThis; String), window / globalThis "
              "writes inside a function, defineProperty(window, 'x'); no interface class for a global augmentation",
              gp == want_g and not pt["src/g.ts"].classes, (sorted(gp ^ want_g), pt["src/g.ts"].classes))
    mp = {(p[0].name, p[1]) for p in pt["src/m.js"].patches}
    want_m = {("K", "a"), ("K", "b"), ("K", "c"), ("K", "d"), ("K", "*"), ("K", "e"), ("K", "g"), ("L", "h"),
              ("L", "*"), ("S", "*"), ("T", "find"), ("U", "*")}
    rec.check("C prototype mixins from any file: every key of an object-literal source (inline, or a module-level "
              "literal with its later `x.k =` writes), the one member a spy / stub names (`jest.spyOn(T.prototype, "
              "'find')`), '*' for a source not read (a call, `sinon.stub(U.prototype)`) or a whole-prototype write; "
              "only the FIRST argument is written (setPrototypeOf(S.prototype, B.prototype) leaves B alone); "
              "read-only uses (Object.keys, console.log, getOwnPropertyNames) patch nothing",
              want_m <= mp and not {x for x in mp if x[0] in ("K", "L", "S", "T", "U")} - want_m
              and not any(x[0] in ("R", "B") for x in mp), sorted(mp))
    # writes to a field the reader cannot type
    fw = snip("export class S {\n  repo = new Repo();\n  store = new Repo();\n  keep = new Repo();\n"
              "  a(): void {\n    const self = this;\n    self.repo = new Cache();\n  }\n  b(): void {\n"
              "    [1].forEach(function () {\n      this.store = new Cache();\n    });\n  }\n"
              "  c(o: any): void {\n    o.keep = 1;\n  }\n}\n"
              "export class D2 {\n  m(): void {\n    [1].forEach(function () {\n      Object.assign(this, {});\n"
              "    });\n  }\n}\nexport class D3 {\n  m(): void {\n    const self = this;\n    Object.assign(self, {});\n"
              "  }\n}\nexport class D4 {\n  m(): void {\n    Object.assign({}, this);\n  }\n}\n")
    s = fw.classes[0]
    rec.check("C a write through a `this` alias (`const self = this; self.f = ...`) or `this.f = ...` in a callback "
              "nested in the class is an untyped binding of f; TypeScript keeps a field another object writes",
              "unknown" in [b.kind for b in s.fields["repo"]] and "unknown" in [b.kind for b in s.fields["store"]]
              and [b.kind for b in s.fields["keep"]] == ["ctor"], {k: [b.kind for b in v] for k, v in s.fields.items()})
    rec.check("C Object.assign onto `this` in a callback nested in the class, or onto a `this` alias, makes the class "
              "dynamic; reading `this` (Object.assign({}, this)) does not",
              [(c.name, c.dynamic) for c in fw.classes] == [("S", False), ("D2", True), ("D3", True), ("D4", False)],
              [(c.name, c.dynamic) for c in fw.classes])
    jw = snip("class S {\n  constructor() {\n    this.repo = new Repo();\n    this.keep = new Repo();\n  }\n}\n"
              "function swap(s) {\n  s.repo = 1;\n}\n", "snip.js")
    rec.check("C JavaScript: any in-file `x.f = ...` write makes field f of the file's classes untyped (no declared "
              "type holds it)", [b.kind for b in jw.classes[0].fields["repo"]] == ["ctor", "unknown"]
              and [b.kind for b in jw.classes[0].fields["keep"]] == ["ctor"],
              {k: [b.kind for b in v] for k, v in jw.classes[0].fields.items()})
    ja = snip("class K {\n  m(a, b) {}\n}\nfunction f(a) {}\nconst g = (a, b) => a;\n", "snip.js")
    rec.check("C JavaScript arity is (0, -1): no overloads, any argument count is legal",
              ja.classes[0].methods["m"][0].arity == (0, -1) and ja.functions["f"][0].arity == (0, -1)
              and ja.functions["g"][0].arity == (0, -1))


def mini_link(files: dict, nodes_spec: list, calls_spec: list, mod=None):
    """typed_calls.link over an in-memory tree with synthetic engine nodes (label, name, line) of its one file and
    name tuples (caller name, callee) -> (remaining name tuples, stats, {(caller, target name)})."""
    m = mod or R
    (rel,) = list(files)
    root = write_tree(tmp() / f"mini{len(_ROOTS)}_{abs(hash(files[rel])) % 10**8}", files)
    nodes = [{"key": f"k_{name}", "label": label, "name": name, "source_file": rel, "start_line": line}
             for label, name, line in [("Module", "a", 1)] + list(nodes_spec)]
    calls = [(f"k_{c}", callee) for c, callee in calls_spec]
    readers = {lang: m.make_reader(TC.LANGUAGES[lang]) for lang in ("typescript", "javascript")}
    out, st = TC.link(str(root), nodes, calls, env=ON, readers=readers, source=GX._Source(root),
                      is_test=lambda p: False)
    name = {n["key"]: n["name"] for n in nodes}
    rest = [(name[a], str(b)) for a, b in out if not GX.is_call_key(b)]
    typed = {(name[a], name[str(b)]) for a, b in out if GX.is_call_key(b)}
    return rest, st, typed


_TEXT_N = [0]


def facts_of_text(files: dict, mod=None) -> dict:
    m = mod or R
    _TEXT_N[0] += 1
    root = write_tree(tmp() / f"text{_TEXT_N[0]}", files)
    src = GX._Source(root)
    out = {}
    for rel in files:
        lang = "javascript" if rel.lower().endswith((".js", ".jsx", ".mjs", ".cjs")) else "typescript"
        out[rel] = m.make_reader(TC.LANGUAGES[lang]).facts(rel, src)
    return out


# ================================================================================================
# D: the binder
# ================================================================================================

def binder_for(mod=None):
    m = mod or R
    root = root_for("binder", BINDER)
    facts = {}
    src = GX._Source(root)
    for rel in sorted(BINDER):
        d = m.dialect_of(rel)
        if d is None:
            continue
        lang = "javascript" if d == "js" else "typescript"
        facts[rel] = m.make_reader(TC.LANGUAGES[lang]).facts(rel, src)
    graph = frozenset(r for r in facts if r not in BINDER_NON_GRAPH)
    for r in BINDER_NON_GRAPH:
        facts.pop(r, None)
    ctx = TC.BinderContext(root=str(root), lang="typescript", rels=tuple(sorted(graph)), facts=facts,
                           graph_files=graph)
    return m.make_reader(TC.LANGUAGES["typescript"]).binder(ctx)


def sec_binder(rec: Rec, mod=None) -> None:
    T = TC
    b = binder_for(mod)
    U = "src/use.ts"

    def res(kind, spec, name="", local="x"):
        return b.resolve_import(U, T.Import(local, kind, spec, name))

    sym = lambda rel, n: T.Resolution("symbol", rel=rel, name=n)          # noqa: E731
    modr = lambda rel: T.Resolution("module", rel=rel)                    # noqa: E731
    cases = [
        ("a name two `export *` modules define is unknown", res("named", "./esm/stars", "A"), T.RES_UNKNOWN),
        ("a name one `export *` module defines", res("named", "./esm/stars", "onlyC"), sym("src/esm/c.ts", "onlyC")),
        ("...unknown when another star is a package (it may export it too)", res("named", "./esm/stars2", "onlyC"),
         T.RES_UNKNOWN),
        ("a name only a package star can export is external", res("named", "./esm/stars2", "zzz"),
         T.Resolution("external", stems=("lodash",))),
        ("a name no star exports is unknown", res("named", "./esm/stars", "zzz"), T.RES_UNKNOWN),
        ("tsconfig `paths`: `import * as u from '@x/util'` -> module(util.ts) (plain)",
         res("namespace", "@x/util", "*"), modr("src/x/util.ts")),
        ("`export { g as f }` beside a local f -> a namespace token, never module()",
         res("namespace", "./esm/ren", "*"), T.Resolution("namespace", name="esm|src/esm/ren.ts")),
        ("a rename with no colliding local stays module()", res("namespace", "./esm/ren2", "*"), modr("src/esm/ren2.ts")),
        ("`export default class D` -> D", res("default", "./esm/def", "default"), sym("src/esm/def.ts", "D")),
        ("`export default { ... }` -> unknown", res("default", "./esm/defexpr", "default"), T.RES_UNKNOWN),
        ("`export * as util from` -> module(util.ts)", res("named", "./esm/nsre", "util"), modr("src/x/util.ts")),
        ("NodeNext `./esm/node.js` names node.ts", res("named", "./esm/node.js", "nn"), sym("src/esm/node.ts", "nn")),
        ("require of `module.exports = W` IS W", res("module", "./cjs/whole", "="), sym("src/cjs/whole.js", "W")),
        ("require of `module.exports = { a }` -> module(obj.js)", res("module", "./cjs/obj", "="), modr("src/cjs/obj.js")),
        ("a barrel's `module.exports.obj = require('./obj')`", res("named", "./cjs/barrel", "obj"), modr("src/cjs/obj.js")),
        ("...and `module.exports.whole = require('./whole')` is W", res("named", "./cjs/barrel", "whole"),
         sym("src/cjs/whole.js", "W")),
        ("`exports.q = q` with q declared -> module(props.js)", res("module", "./cjs/props", "="),
         modr("src/cjs/props.js")),
        ("`module.exports = { list: other }` beside a private list -> a namespace token",
         res("module", "./cjs/ren", "="), T.Resolution("namespace", name="cjs|src/cjs/ren.js")),
        ("a computed `exports[k] =` -> unknown", res("module", "./cjs/dyn", "="), T.RES_UNKNOWN),
        ("`module.exports = new S()` -> unknown", res("module", "./cjs/inst", "="), T.RES_UNKNOWN),
        ("`module.exports = require('./obj')` -> module(obj.js)", res("module", "./cjs/reexp", "="),
         modr("src/cjs/obj.js")),
        ("`import * as W from` a `module.exports = W` module -> unknown (interop)", res("namespace", "./cjs/whole", "*"),
         T.RES_UNKNOWN),
        ("a default import of a CommonJS object -> module(obj.js)", res("default", "./cjs/obj", "default"),
         modr("src/cjs/obj.js")),
        ("`path` -> external", res("named", "path", "join"), T.Resolution("external", stems=("path",))),
        ("a scoped package -> external (@scope/pkg stem)", res("named", "@nestjs/common", "Injectable"),
         T.Resolution("external", stems=("@nestjs/common",))),
        ("node:fs -> external", res("namespace", "node:fs", "*"), T.Resolution("external", stems=("node:fs",))),
        ("an alias no config maps -> unknown", res("named", "@/nothing", "x"), T.RES_UNKNOWN),
        ("a missing relative module -> unknown", res("named", "./nope", "x"), T.RES_UNKNOWN),
        ("a workspace package by its package.json name", res("named", "@ws/pk", "P"),
         sym("packages/pk/src/index.ts", "P")),
        ("a file outside the graph binds nothing (never another candidate)", res("named", "./esm/hidden", "secret"),
         T.RES_UNKNOWN),
        ("a dependency the package.json above declares -> external (its package stem alone)",
         res("named", "lodash/fp", "get"), T.Resolution("external", stems=("lodash",))),
        ("...a declared scoped one too", res("named", "@acme/ui/button", "B"),
         T.Resolution("external", stems=("@acme/ui",))),
        ("a package no package.json above declares -> unknown (an alias no config maps, or undeclared)",
         res("named", "undeclared-pkg", "x"), T.RES_UNKNOWN),
        ("a custom-scope alias (`@utils/format`, Vite / webpack) -> unknown", res("named", "@utils/format", "f"),
         T.RES_UNKNOWN),
        ("a `$` alias (`$lib/money`, SvelteKit) -> unknown", res("named", "$lib/money", "f"), T.RES_UNKNOWN),
        ("a Node builtin with a subpath -> external", res("named", "fs/promises", "readFile"),
         T.Resolution("external", stems=("fs",))),
        ("`export *` beside a private local the star target exports -> a namespace token, never module()",
         res("namespace", "./esm/starlocal", "*"), T.Resolution("namespace", name="esm|src/esm/starlocal.ts")),
        ("`export *` with no such collision stays module()", res("namespace", "./esm/starclean", "*"),
         modr("src/esm/starclean.ts")),
    ]
    for desc, got, want in cases:
        rec.check(f"D {desc}", got == want, got)
    rec.check("D ...the token's member is the star export (`onlyC` -> c.ts), never the private local",
              b.submodule("esm|src/esm/starlocal.ts", "onlyC") == sym("src/esm/c.ts", "onlyC"),
              b.submodule("esm|src/esm/starlocal.ts", "onlyC"))
    m = mod or R
    stems = {s: m.spec_stems(s) for s in ("@utils/format", "@app/services/repo.service", "$lib/money", "~x", "#x",
                                          "_x", "lodash", "lodash/fp", "@nestjs/common", "node:fs", "fs/promises")}
    rec.check("D spec_stems (no package.json above the file): an alias's every segment is a stem the core's test "
              "checks; `$` / `~` / `#` / `_` can never name a package (unknown); a known package keeps its stem",
              {"utils", "format"} <= set(stems["@utils/format"])
              and {"app", "services", "repo.service", "repo"} <= set(stems["@app/services/repo.service"])
              and stems["$lib/money"] == () and stems["~x"] == () and stems["#x"] == () and stems["_x"] == ()
              and stems["lodash"] == ("lodash",) and stems["lodash/fp"] == ("lodash", "fp")
              and stems["@nestjs/common"] == ("@nestjs/common",) and stems["node:fs"] == ("node:fs",)
              and stems["fs/promises"] == ("fs",), stems)
    rec.check("D submodule: a namespace token's member is its export exactly (`list` -> `other`, never the "
              "private `list`)", b.submodule("cjs|src/cjs/ren.js", "list") == sym("src/cjs/ren.js", "other"),
              b.submodule("cjs|src/cjs/ren.js", "list"))
    rec.check("D submodule: module(ren2.ts)'s `h` -> g; a property defined in place (`exports.p = function`) -> "
              "None", b.submodule("src/esm/ren2.ts", "h") == sym("src/esm/ren2.ts", "g")
              and b.submodule("src/cjs/props.js", "p") is None)
    if core_has("loose"):
        rec.check("D resolve_free: None — the core's PATCH_RULES 'loose' judges a patched global's members itself, "
                  "so the binder's per-global rule stands down (one rule)",
                  b.resolve_free(U, "Order") is None and b.resolve_free(U, "window") is None,
                  (b.resolve_free(U, "Order"), b.resolve_free(U, "window")))
    else:
        rec.check("D resolve_free: None (TS / JS globals are the LanguageSpec builtins) — but a builtin global the "
                  "project patches (`window.x = ...`) is unknown in every file, never an external receiver (until the "
                  "core declares PATCH_RULES 'loose')",
                  b.resolve_free(U, "Order") is None and b.resolve_free(U, "document") is None
                  and b.resolve_free(U, "window") == T.RES_UNKNOWN,
                  (b.resolve_free(U, "Order"), b.resolve_free(U, "document"), b.resolve_free(U, "window")))


# ================================================================================================
# E: end to end on both engines' captures
# ================================================================================================

def sec_e2e(rec: Rec, mod=None, buckets=None, trees=None) -> None:
    caps = {(b, n): load_capture(b, n) for b in (buckets or BUCKETS.values()) for n in (trees or TREES)}
    missing = [k for k, v in caps.items() if v is None]
    rec.check("E every tree is captured on graphify 0.8.39 and 0.8.51", not missing, missing)
    stale = [k for k, v in caps.items() if v is not None and v.get("tree_sha256") != tree_sha(k[1])]
    rec.check("E every capture was made from this tree (re-capture after editing one)", not stale, stale)
    by_bucket: dict = {}
    for (b, name), cap in sorted(caps.items()):
        if cap is None:
            continue
        e = EXPECT[name]
        root = root_for(name)
        plain = pipeline(cap["graph"], root, ON, mod=mod)
        inj = pipeline(cap["graph"], root, ON, e["inject"], mod=mod)
        by_bucket[(b, name)] = (plain.typed(), plain.removed())
        tag = f"E {b}/{name}"
        rec.check(f"{tag}: no error, every file read, no language disabled",
                  not plain.stats["error"] and not plain.stats["files_failed"] and not plain.stats["langs_disabled"],
                  (plain.stats["error"], plain.stats["files_failed"], plain.stats["langs_disabled"]))
        got = plain.typed()
        rec.check(f"{tag}: the typed set and grades are exactly the expected ({len(e['typed'])})",
                  got == e["typed"], {"missing": {k: v for k, v in e["typed"].items() if got.get(k) != v},
                                      "extra": {k: v for k, v in got.items() if k not in e["typed"]}})
        rem = plain.removed()
        rec.check(f"{tag}: REPLACE removes exactly the expected engine tuples ({len(e['engine_removed'])})",
                  rem == e["engine_removed"], {"missing": e["engine_removed"] - rem, "extra": rem - e["engine_removed"]})
        rec.check(f"{tag}: the engine tuples of unknown sites stay",
                  all(plain.has_name_tuple(c, n) for c, n in e["engine_kept"]),
                  [p for p in e["engine_kept"] if not plain.has_name_tuple(*p)])
        rec.check(f"{tag}: test-caller policy: pure additions skipped = {e['skipped_policy']}",
                  plain.stats["skipped_policy"] == e["skipped_policy"], plain.stats["skipped_policy"])
        irem = inj.removed() - e["engine_removed"]
        rec.check(f"{tag}: injected name tuples: removed exactly where every site is typed / external / absent",
                  irem == e["inject_removed"], {"missing": e["inject_removed"] - irem, "extra": irem - e["inject_removed"]})
        rec.check(f"{tag}: injected name tuples of untyped sites (the §5 must-nots) stay",
                  all(inj.has_name_tuple(c, n) for c, n in e["inject_kept"]),
                  [p for p in e["inject_kept"] if not inj.has_name_tuple(*p)])
        want_inj = dict(e["typed"])
        want_inj.update(e["inject_typed"])
        rec.check(f"{tag}: with the injected tuples the typed set is the expected (+ the test caller's correction)",
                  inj.typed() == want_inj, {"missing": {k: v for k, v in want_inj.items() if inj.typed().get(k) != v},
                                            "extra": {k: v for k, v in inj.typed().items() if k not in want_inj}})
        mustnot = [c for c in e["mustnot"] if any(k[0] == c for k in got)]
        rec.check(f"{tag}: the must-not callers get no typed key ({len(e['mustnot'])})", not mustnot, mustnot)
        bad = []
        for run in (plain, inj):
            for c in run.out:
                if not GX.is_call_key(c[1]):
                    continue
                t = run.node.get(c[1])
                caller = run.node.get(c[0])
                if t is None or caller is None:
                    bad.append(("not a node", c))
                    continue
                tf, cf = str(t.get("source_file")), str(caller.get("source_file"))
                if tf.endswith(".py"):
                    bad.append(("a Python target", c))
                if run.id_of[c[1]] in NEVER_TARGETS:
                    bad.append(("a never-target", c))
                g = (caller.get("call_typed") or {}).get(c[1])
                if g not in ("EXTRACTED", "INFERRED", "AMBIGUOUS"):
                    bad.append(("no call_typed grade", c))
                if g == "EXTRACTED" and tf != cf:
                    bad.append(("EXTRACTED across files", c))
                if c[0] == c[1]:
                    bad.append(("a self target", c))
        rec.check(f"{tag}: every typed key names a node of `nodes`, never a .py node or a private name, carries a "
                  f"call_typed grade, EXTRACTED only in-file, never the caller", not bad, bad[:6])
        # flags
        off = pipeline(cap["graph"], root, {}, mod=mod)
        rec.check(f"{tag}: flags off -> the SAME list object, no node attribute",
                  off.out is off.inp and not any("call_typed" in n or "call_weak" in n for n in off.nodes))
        lang = "javascript" if name.startswith("ts") else "typescript"
        other = pipeline(cap["graph"], root, {"DEEPMAP_TYPED_CALLS": lang}, mod=mod)
        rec.check(f"{tag}: only the OTHER language on -> nothing typed here (same list object)",
                  other.out is other.inp and not other.typed(), (other.typed(), other.stats["error"]))
        only_c = pipeline(cap["graph"], root, dict(ON, DEEPMAP_TYPED_CALLS_SHAPES="C"), mod=mod)
        want_c = {k: v for k, v in e["typed"].items()
                  if plain.node[plain.key_of[k[1]]].get("label") == "Class"}
        rec.check(f"{tag}: SHAPES=C -> exactly the constructor edges", only_c.typed() == want_c,
                  (only_c.typed(), want_c))
        norep = pipeline(cap["graph"], root, dict(ON, DEEPMAP_TYPED_CALLS_REPLACE="0"), e["inject"], mod=mod)
        want_norep = {k: v for k, v in want_inj.items() if k not in e.get("inject_rekeyed", ())}
        rec.check(f"{tag}: REPLACE=0 -> nothing removed, the same typed set (but the overrides a REPLACE re-keys: "
                  f"nothing removed their fan-out)",
                  not norep.removed() and norep.typed() == want_norep, (norep.removed(), norep.typed() == want_norep))
        again, st2 = TC.link(str(root), plain.nodes, list(plain.out), env=ON, is_test=S._is_test_path,
                             readers=None if mod is None else {
                                 lg: mod.make_reader(TC.LANGUAGES[lg]) for lg in ("typescript", "javascript")})
        rec.check(f"{tag}: idempotent (a second pass over the output gives the same tuples)",
                  sorted(plain.pairs(again)) == sorted(plain.pairs(plain.out)) and not st2["error"])
    for name in (trees or TREES):
        a, c = by_bucket.get(("0839", name)), by_bucket.get(("0851", name))
        if a is not None and c is not None:
            rec.check(f"E parity {name}: 0.8.39 and 0.8.51 give the same typed keys, grades and removals",
                      a == c, (a, c))


# ================================================================================================
# F: the installed engine
# ================================================================================================

def real_graph(name: str):
    home = tmp() / "home_real"
    root = write_tree(tmp() / "real" / name, TREES[name])
    with clean_env(DEEPMAP_HOME=str(home)):
        res, rep = GX.extract_with_report(root)
        gp = GX.graphify_out_dir(root) / "graph.json"
        graph = json.loads(gp.read_text(encoding="utf-8")) if (res is not None and gp.is_file()) else None
    return root, graph, rep


def sec_real(rec: Rec, mod=None) -> None:
    if importlib.util.find_spec("graphify") is None:
        print("  [SKIP] graphify is not installed")
        return
    ver = GX.graphify_version()
    b = BUCKETS.get(ver)
    print(f"  (graphify {ver})")
    if b is None:
        print(f"  (no capture for graphify {ver}: only 0.8.39 and 0.8.51 are captured; comparison skipped)")
        return
    for name in TREES:
        root, graph, rep = real_graph(name)
        cap = load_capture(b, name)
        if graph is None or cap is None:
            rec.check(f"F REAL {name}: the engine produced a graph and the capture exists", False,
                      (rep or {}).get("cause"))
            continue
        e = EXPECT[name]
        run = pipeline(graph, root, ON, e["inject"])
        ref = pipeline(cap["graph"], root_for(name), ON, e["inject"])
        rec.check(f"F REAL {name}: the installed engine's fresh extraction gives the captured typed set, grades and "
                  f"removals", run.typed() == ref.typed() and run.removed() == ref.removed(),
                  {"typed+": {k: v for k, v in run.typed().items() if k not in ref.typed()},
                   "typed-": {k: v for k, v in ref.typed().items() if k not in run.typed()},
                   "removed": (run.removed() ^ ref.removed())})


# ================================================================================================
# H: shapes whose fix is a rule of the core (static_split, PATCH_RULES loose / star)
# ================================================================================================

def core_has(feature: str) -> bool:
    """Does build/typed_calls.py carry this S3c-requested rule? "static_split" (LanguageSpec.static_split: a TS / JS
    static member lives on the class object only, an instance one on instances only), "loose" / "star"
    (PATCH_RULES: an attr patched onto anything but one project class is never an external member call; a '*' patch
    makes the class dynamic)."""
    if feature == "static_split":
        import dataclasses
        return any(f.name == "static_split" for f in dataclasses.fields(TC.LanguageSpec)) \
            and bool(getattr(TC.LANGUAGES["typescript"], "static_split", False))
    return feature in getattr(TC, "PATCH_RULES", frozenset())


def mini_link_files(files: dict, nodes_spec: list, calls_spec: list, mod=None):
    """typed_calls.link over an in-memory multi-file tree with synthetic engine nodes [(rel, label, name, line)] and
    name tuples [(caller name, callee)] -> (remaining name tuples, stats, {(caller, target name)})."""
    m = mod or R
    _TEXT_N[0] += 1
    root = write_tree(tmp() / f"multi{_TEXT_N[0]}", files)
    nodes = [{"key": f"k_{rel}_{name}", "label": label, "name": name, "source_file": rel, "start_line": line}
             for rel, label, name, line in list(nodes_spec)]
    key = {n["name"]: n["key"] for n in nodes}
    calls = [(key[c], callee) for c, callee in calls_spec]
    readers = {lang: m.make_reader(TC.LANGUAGES[lang]) for lang in ("typescript", "javascript")}
    out, st = TC.link(str(root), nodes, calls, env=ON, readers=readers, source=GX._Source(root),
                      is_test=lambda p: False)
    name = {n["key"]: n["name"] for n in nodes}
    rest = [(name[a], str(b)) for a, b in out if not GX.is_call_key(b)]
    typed = {(name[a], name[str(b)]) for a, b in out if GX.is_call_key(b)}
    return rest, st, typed


def sec_core(rec: Rec, mod=None) -> None:
    st_src = ("export class A {\n  static create(): A {\n    return new A();\n  }\n\n  create(): number {\n"
              "    return 1;\n  }\n}\n\nexport function useInst(): number {\n  const a = new A();\n"
              "  return a.create();\n}\n\nexport function useStatic(): A {\n  return A.create();\n}\n\n"
              "export class C {\n  static make(): number {\n    return this.helper();\n  }\n\n"
              "  helper(): number {\n    return 1;\n  }\n}\n")
    rest, st, typed = mini_link_files(
        {"src/st.ts": st_src},
        [("src/st.ts", "Module", "st", 1), ("src/st.ts", "Class", "A", 1),
         ("src/st.ts", "Method", "A.create", 2),                                      # one node (both engines)
         ("src/st.ts", "Function", "useInst", 11), ("src/st.ts", "Function", "useStatic", 16),
         ("src/st.ts", "Class", "C", 20), ("src/st.ts", "Method", "C.make", 21), ("src/st.ts", "Method", "C.helper", 25)],
        [("useInst", "create"), ("useStatic", "create"), ("C.make", "helper")], mod)
    desc = ("H static vs instance: `a.create()` never binds the static `create` node (the instance one has none), "
            "`A.create()` does; a static method's `this.helper()` of an instance-only helper is absent (TS)")
    rec.check("H the core declares LanguageSpec.static_split for TypeScript and JavaScript", core_has("static_split"))
    rec.check(desc, ("useInst", "A.create") not in typed and ("useInst", "create") in rest
              and ("useStatic", "A.create") in typed and ("useStatic", "create") not in rest
              and ("C.make", "C.helper") not in typed and ("C.make", "helper") not in rest and not st["error"],
              (sorted(typed), rest, st["error"]))
    rest, st, typed = mini_link_files(
        {"src/p.js": "String.prototype.cap = function () {\n  return 1;\n};\n\nexport function greet() {\n"
                     "  return 'x'.cap();\n}\n\nexport function plain() {\n  return 'x'.trim();\n}\n",
         "src/q.ts": "export function f(s: string): number {\n  return s.cap();\n}\n"},
        [("src/p.js", "Module", "p", 1), ("src/p.js", "Function", "greet", 5), ("src/p.js", "Function", "plain", 9),
         ("src/q.ts", "Module", "q", 1), ("src/q.ts", "Function", "f", 1)],
        [("greet", "cap"), ("plain", "trim"), ("f", "cap")], mod)
    desc = ("H a builtin's prototype the project patches (`String.prototype.cap = ...`): `'x'.cap()` and "
            "`s.cap()` (s: string) are unknown, never external; an unpatched `'x'.trim()` stays external")
    rec.check("H the core declares PATCH_RULES 'loose'", core_has("loose"))
    rec.check(desc, ("greet", "cap") in rest and ("f", "cap") in rest and ("plain", "trim") not in rest
              and not st["error"], (rest, st["error"]))
    rest, st, typed = mini_link_files(
        {"src/k.js": "export class K {\n  find() {\n    return 1;\n  }\n\n  run() {\n    return this.find();\n  }\n}\n",
         "src/m.js": "import { K } from './k';\nimport { mk } from './mk';\n\nObject.assign(K.prototype, mk());\n",
         "src/mk.js": "export function mk() {\n  return {};\n}\n"},
        [("src/k.js", "Module", "k", 1), ("src/k.js", "Class", "K", 1), ("src/k.js", "Method", "K.find", 2),
         ("src/k.js", "Method", "K.run", 6), ("src/m.js", "Module", "m", 1), ("src/mk.js", "Module", "mk", 1),
         ("src/mk.js", "Function", "mk", 1)],
        [("K.run", "find")], mod)
    desc = ("H a mixin no file reads (`Object.assign(K.prototype, mk())` in another file) makes K dynamic: "
            "`this.find()` is unknown, its tuple stays")
    rec.check("H the core declares PATCH_RULES 'star'", core_has("star"))
    rec.check(desc, ("K.run", "K.find") not in typed and ("K.run", "find") in rest and not st["error"],
              (sorted(typed), rest, st["error"]))


# ================================================================================================
# I: dynamic dispatch and patched builtin prototypes on the real engine (both captures)
# ================================================================================================

# (tree, caller, member, a project OVERRIDE run-time dispatch reaches): the engine's tuple reaches it before link and
# still does after (kept whole with its own grade, or re-emitted as a key at AMBIGUOUS)
DISPATCH_KEEP = [("ts_dispatch", "src/shapes.ts::use", "m", "src/shapes.ts::Sub.m"),
                 ("ts_dispatch", "src/shapes.ts::Base.run", "m", "src/shapes.ts::Sub.m"),
                 ("ts_dispatch", "src/sub2.ts::viaSub2", "m", "src/sub2.ts::Sub2.m"),
                 ("js_dispatch", "src/jsd.js::JBase.run", "m", "src/jsd.js::JSub.m")]
# (tree, caller, member, a same-named method dispatch can NOT reach): reached before, gone after
DISPATCH_GONE = [("ts_dispatch", "src/shapes.ts::onLeaf", "m", "src/shapes.ts::Sub.m"),
                 ("ts_dispatch", "src/shapes.ts::made", "m", "src/shapes.ts::Sub.m"),
                 ("js_dispatch", "src/jsd.js::jmade", "m", "src/jsd.js::JSub.m")]
# with the injected `m` fan-out (every m of the family): what stays reachable, exactly
DISPATCH_FANOUT = {("ts_dispatch", "src/shapes.ts::use"): {"src/shapes.ts::Base.m", "src/shapes.ts::Sub.m",
                                                           "src/sub2.ts::Sub2.m"},
                   ("ts_dispatch", "src/shapes.ts::onLeaf"): {"src/shapes.ts::Leaf.m"},
                   ("ts_dispatch", "src/shapes.ts::made"): {"src/shapes.ts::Base.m"},
                   ("ts_dispatch", "src/sub2.ts::viaSub2"): {"src/shapes.ts::Base.m", "src/shapes.ts::Sub.m",
                                                             "src/sub2.ts::Sub2.m"},
                   ("js_dispatch", "src/jsd.js::JBase.run"): {"src/jsd.js::JBase.m", "src/jsd.js::JSub.m"}}


def reach(run, calls, caller_id, member) -> set:
    """`file::name` of every node the caller's tuples of `member` can bind at the push (a key: itself; a name: its
    fan-out through the push's name index)."""
    import edge_resolve as ER  # noqa: WPS433
    ck = run.key_of.get(caller_id)
    idx = ER.build_name_index([{"key": n["key"], "name": n.get("name")} for n in run.nodes])
    name_of = {n["key"]: n.get("name") for n in run.nodes}
    out = set()
    for c, t in calls:
        if c != ck or not TC.callee_matches(t, member, name_of, TC._is_call_key):
            continue
        keys = [str(t)] if GX.is_call_key(t) else [k for k in idx.get(t, ()) if k != ck]
        out.update(run.id_of.get(k, k) for k in keys)
    return out


def sec_dispatch(rec: Rec, mod=None) -> None:
    for b in BUCKETS.values():
        runs, injected = {}, {}
        for name in ("ts_dispatch", "js_dispatch", "ts_globals"):
            cap = load_capture(b, name)
            if cap is None:
                rec.check(f"I {b}/{name}: captured", False)
                continue
            runs[name] = pipeline(cap["graph"], root_for(name), ON, mod=mod)
            injected[name] = pipeline(cap["graph"], root_for(name), ON, EXPECT[name]["inject"], mod=mod)
        for tree, caller, member, over in DISPATCH_KEEP:
            run = runs.get(tree)
            if run is None:
                continue
            pre, post = reach(run, run.before, caller, member), reach(run, run.out, caller, member)
            rec.check(f"I {b}/{tree} {caller} `.{member}()`: the engine's tuple reaches the override {over} before "
                      f"link (teeth) and still does after", over in pre and over in post, (sorted(pre), sorted(post)))
            node = run.node.get(run.key_of.get(caller), {})
            rec.check(f"I {b}/{tree} {caller}: the kept engine tuple keeps its own grade (no typed grade for {over})",
                      run.key_of.get(over) not in (node.get("call_typed") or {}), node.get("call_typed"))
        for tree, caller, member, gone in DISPATCH_GONE:
            run = runs.get(tree)
            if run is None:
                continue
            pre, post = reach(run, run.before, caller, member), reach(run, run.out, caller, member)
            rec.check(f"I {b}/{tree} {caller} `.{member}()`: {gone} (dispatch cannot reach it) is removed",
                      gone in pre and gone not in post, (sorted(pre), sorted(post)))
        for (tree, caller), want in DISPATCH_FANOUT.items():
            run = injected.get(tree)
            if run is None:
                continue
            post = reach(run, run.out, caller, "m")
            rec.check(f"I {b}/{tree} {caller}: with an `m` fan-out injected, exactly the static target and the "
                      f"overrides stay reachable ({len(want)})", post == want, sorted(post ^ want))
            node = run.node.get(run.key_of.get(caller), {})
            bad = {run.id_of.get(t, t): g for t, g in (node.get("call_typed") or {}).items()
                   if run.id_of.get(t, t) in want and g != "AMBIGUOUS"
                   and (caller, run.id_of.get(t, t)) not in EXPECT[tree]["typed"]}
            rec.check(f"I {b}/{tree} {caller}: an override re-emitted from a removed fan-out is AMBIGUOUS (never an "
                      f"upgrade)", not bad, bad)
        run = runs.get("ts_globals")
        if run is not None:
            kept = [(c, n) for c, n in sorted(EXPECT["ts_globals"]["engine_kept"]) if not run.has_name_tuple(c, n)]
            rec.check(f"I {b}/ts_globals: the REAL engine's tuples of a literal / string-typed receiver's call of a "
                      f"member the project patches onto String.prototype stay (PATCH_RULES loose)",
                      not kept and not run.removed() and run.stats["sites"]["external"] == 0,
                      (kept, sorted(run.removed()), run.stats["sites"]))
        st = runs.get("ts_dispatch")
        if st is not None:
            rec.check(f"I {b}/ts_dispatch: stats count the kept override tuples (override_kept) and no group fell "
                      f"back for an unknowable reach", st.stats.get("override_kept") == 3
                      and st.stats["vetoes"].get("dispatch") == 0, (st.stats.get("override_kept"), st.stats["vetoes"]))


# ================================================================================================
# K: an absent tree-sitter binding / grammar (an OPTIONAL dependency)
# ================================================================================================

_NO_GRAMMAR = r"""
import importlib.abc, json, sys
class _Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0].startswith("tree_sitter"):
            raise ImportError(f"blocked: {name}")
        return None
sys.meta_path.insert(0, _Block())
sys.path.insert(0, sys.argv[1])
import typed_calls as TC, receiver_facts_ts as R, graphify_extract as GX
root = sys.argv[2]
nodes = [{"key": "a::use", "label": "Function", "name": "use", "source_file": "src/a.ts", "start_line": 3},
         {"key": "a::A.m", "label": "Method", "name": "A.m", "source_file": "src/a.ts", "start_line": 2},
         {"key": "a::A", "label": "Class", "name": "A", "source_file": "src/a.ts", "start_line": 1}]
calls = [("a::use", "m")]
out, st = TC.link(root, nodes, calls, env={"DEEPMAP_TYPED_CALLS": "typescript,javascript",
                                          "DEEPMAP_CALLS_AUDIT": "typescript,javascript"},
                  is_test=lambda p: False, source=GX._Source(root))
try:
    R.parse_bytes(b"x", "ts")
    raised = ""
except Exception as exc:
    raised = type(exc).__name__ + ("/RuntimeError" if isinstance(exc, RuntimeError) else "")
print(json.dumps({"same": out is calls, "disabled": st["langs_disabled"], "error": st["error"],
                  "attrs": [k for n in nodes for k in ("call_typed", "call_weak") if k in n],
                  "available": getattr(R, "grammars_available", lambda: None)(), "raised": raised,
                  "loaded": sorted(m for m in sys.modules if m.startswith("tree_sitter"))}))
"""


def sec_no_grammar(rec: Rec, mod=None) -> None:
    if mod is not None:
        return
    root = write_tree(tmp() / "nogrammar", {"src/a.ts": "export class A {\n  m(): void {}\n}\nexport function use(a: A) "
                                                       "{\n  a.m();\n}\n"})
    script = tmp() / "nogrammar.py"
    script.write_text(_NO_GRAMMAR, encoding="utf-8")
    proc = subprocess.run([sys.executable, "-X", "utf8", str(script), str(BUILD), str(root)], capture_output=True,
                          text=True, encoding="utf-8", timeout=120)
    try:
        got = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        rec.check("K the no-grammar probe ran", False, (proc.returncode, proc.stderr[-800:]))
        return
    rec.check("K tree-sitter absent: both languages are disabled by the canary, link() returns its input list "
              "unchanged (the same object), no error, no node attribute",
              got["same"] and got["disabled"] == {"typescript": "canary", "javascript": "canary"}
              and not got["error"] and not got["attrs"], got)
    rec.check("K tree-sitter absent: the reader reports the grammars unavailable (grammars_available False; "
              "parse_bytes raises GrammarUnavailable, a RuntimeError) and no tree_sitter module got loaded",
              got["available"] is False and got["raised"] == "GrammarUnavailable/RuntimeError" and not got["loaded"],
              got)


# ================================================================================================
# G: mutation teeth
# ================================================================================================

# (why, anchor(s), replacement(s), section[, core rule]): a mutation of section "sec_e2e" runs the four trees it was
# first proven RED on, "sec_e2e:<tree>,..." names its trees; a core rule (core_has) that, once present, makes the
# reader's rule stand down skips the mutation.
_BASE_TREES = ("ts_app", "ts_mustnot", "js_app", "js_mustnot")

MUTATIONS = [
    ("a `function` expression sees the class's `this`",
     'return self.enter_function(n, ctx, name, line, lines, -1, "", "function_expr", tp)',
     'return self.enter_function(n, ctx, name, line, lines, ctx.cls, ctx.self_kind, "function_expr", tp)',
     "sec_e2e"),
    # --- the S3c review round
    ("a function expression keeps the legacy Scope.kind (uncertain attribution)",
     'return self.enter_function(n, ctx, name, line, lines, -1, "", "function_expr", tp)',
     'return self.enter_function(n, ctx, name, line, lines, -1, "", "function", tp)', "sec_e2e:ts_mustnot2"),
    ("a destructured local never shadows an outer typed name",
     "                elif name_n is not None:\n                    for nm in self.pattern_names(name_n):\n"
     "                        self.poison[ctx.si].add(nm)\n",
     "                elif name_n is not None:\n                    pass\n", "sec_e2e:ts_mustnot2"),
    ("a catch parameter never shadows an outer typed name",
     "        if p is not None:\n            for nm in self.pattern_names(p):\n                self.poison_name(ctx, nm)\n",
     "        pass\n", "sec_e2e:ts_mustnot2"),
    ("an `import type` name is a value receiver", "            if name in _NONE_IDENTS or name in self.type_only:\n",
     "            if name in _NONE_IDENTS:\n", "sec_e2e:ts_mustnot2"),
    ("a for-of variable never shadows an outer typed name",
     "            for nm in self.pattern_names(left):\n                self.poison_name(ctx, nm)\n",
     "            pass\n", "sec_e2e:ts_mustnot2"),
    ("a `$` / `~` / `#` alias may name a package",
     "    if not head or not (head[0].isascii() and head[0].isalnum()):\n", "    if not head:\n",
     "sec_e2e:js_mustnot2"),
    ("an alias's path segments are no stems", "    out = [stem]\n", "    return (stem,)\n", "sec_e2e:js_mustnot2"),
    ("an undeclared package is external", "        return (package_stem(s),) if package_stem(s) in deps else ()\n",
     "        return (package_stem(s),)\n", "sec_e2e:ts_mustnot2"),
    ("an `export *` barrel's private local is plain", "        if info.stars:\n            own = ",
     "        if info.stars and False:\n            own = ", "sec_e2e:ts_mustnot2,js_mustnot2"),
    ("a local `type` / `enum` never shadows", '                if t in ("type_alias_declaration", "enum_declaration"):\n',
     "                if False:\n", "sec_e2e:ts_mustnot2"),
    ("a local `import X = N.Y` never shadows",
     "            if ctx.si >= 0:\n                self.poison[ctx.si].add(name)\n            elif name not in self.globals:\n"
     "                self.add_global(name, self.unknown(_line(n)))\n", "            pass\n", "sec_e2e:ts_mustnot2"),
    ("a string-literal computed name is no method (the class opens instead)",
     '        if name is None and nm is not None and nm.type == "computed_property_name":\n', "        if False:\n",
     "sec_e2e:ts_mustnot2"),
    ("a computed member leaves the class closed",
     "            self.classes[cidx].open = True               # `[Symbol.iterator]() {}`: a member not named\n",
     "            pass\n", "sec_e2e:ts_mustnot2"),
    ("window / globalThis writes are no patches",
     '            if on in _GLOBAL_OBJECTS or (ctx.si < 0 and on not in ("module", "exports")):\n',
     '            if ctx.si < 0 and on not in ("module", "exports", "window", "globalThis"):\n', "sec_facts"),
    ("`declare global` members are no patches (and make file-level interfaces)",
     '        if any(c.type == "global" for c in n.children):\n', "        if False:\n", "sec_facts"),
    ("a patched builtin global stays external",
     "        return _ir().RES_UNKNOWN if name in self.patched_globals() else None\n", "        return None\n",
     "sec_e2e:ts_mustnot2,js_mustnot2", "loose"),
    ("a write through a `this` alias is ignored", "            c = alias_class(si, name) if name else None\n",
     "            c = None\n", "sec_e2e:ts_mustnot2"),
    ("a callback's `this.f = ...` in a class body is ignored",
     "            if ctx.lex >= 0:                             # `function () { this.f = ... }.bind(this)` in a "
     "class body\n", "            if False:\n", "sec_e2e:ts_mustnot2"),
    ("JavaScript: a field written through another object stays typed", "        if not self.ts:\n            written = ",
     "        if False:\n            written = ", "sec_e2e:js_mustnot2"),
    ("a prototype mixin's keys are no patches", "            self.patches.extend((tref, k) for k in sorted(keys))\n",
     "            pass\n", "sec_e2e:js_mustnot2"),
    ("a mixin source not read adds no '*'", '            if star:\n                self.patches.append((tref, "*"))\n',
     "            pass\n", "sec_facts"),
    ("read-only prototype uses are mixins", "        if not parts or name in _PROTO_READONLY:\n",
     "        if not parts:\n", "sec_facts"),
    ("any argument, not the first, is the mixin target",
     "        parts = self.proto_of(kids[0]) if kids else None\n",
     "        parts = next((p for p in map(self.proto_of, reversed(kids)) if p), None)\n", "sec_facts"),
    ("Object.assign(this) in a callback leaves the class static",
     '        if a0.type == "this" and (ctx.cls >= 0 or ctx.lex >= 0):\n', '        if a0.type == "this" and ctx.cls >= 0:\n',
     "sec_facts"),
    ("Object.assign onto a `this` alias leaves the class static",
     "            c = alias_class(si, name)\n            if c is not None:\n                self.classes[c].dynamic = True\n",
     "            pass\n", "sec_facts"),
    ("JavaScript arity is strict", "        return _arity(_params_node(fn), self.data) if self.ts else (0, -1)\n",
     "        return _arity(_params_node(fn), self.data)\n", "sec_e2e:js_mustnot2"),
    ("receivers nest past the IR's limit", "_MAX_RECV_DEPTH = 30\n", "_MAX_RECV_DEPTH = 48\n", "sec_e2e:ts_mustnot2"),
    ("an arrow forgets the method's `this`",
     'return self.enter_function(n, ctx, name, line, lines, ctx.cls, ctx.self_kind, "arrow", tp)',
     'return self.enter_function(n, ctx, name, line, lines, -1, "", "arrow", tp)', "sec_e2e"),
    ("an `import type` name is a value",
     "        if not parts or parts[0] in self.type_only:\n            return None",
     "        if not parts:\n            return None", "sec_e2e"),
    ("a reassignment never poisons",
     "                    self.poison[s].add(name)\n                    break",
     "                    break", "sec_e2e"),
    ("interfaces are closed",
     'kind="interface", bases=tuple(bases),\n                          open=True,',
     'kind="interface", bases=tuple(bases),\n                          open=False,', "sec_e2e"),
    ("a type alias never shadows a builtin", '        if name and name in self.T.LANGUAGES["typescript"].builtins:',
     "        if False:", "sec_facts"),
    ("a namespace merging into a class ignored",
     '            self.iface_names[(ctx.si, self.text(nm).split(".")[0].strip())] = True', "            pass",
     "sec_facts"),
    ("declaration merging ignored",
     'if cf.kind != "interface" and self.iface_names.get((cf.scope, cf.name)):', "if False:", "sec_e2e"),
    ("a renamed export is plain", 'if k in info.top and tuple(d) != ("local", k):', "if False:", "sec_e2e"),
    ("Object.assign(module.exports) is not dynamic",
     "            self.cjs_done.add(x.start_byte)\n            m.dynamic = True",
     "            self.cjs_done.add(x.start_byte)", "sec_e2e"),
    ("type parameters are classes",
     ["return T.T_UNKNOWN if name in tparams else T.t_name(name)", "type_params=frozenset(tparams),"],
     ["return T.t_name(name)", "type_params=frozenset(),"], "sec_e2e"),
    ("constructor parameter properties are no fields",
     'if ctor >= 0 and any(x.type in ("accessibility_modifier", "readonly", "override")',
     'if False and ctor >= 0 and any(x.type in ("accessibility_modifier", "readonly", "override")', "sec_e2e"),
    ("require() binds no module", 'self.add_import(name, "module", spec, "=", line)',
     "self.add_global(name, self.unknown(line))", "sec_e2e"),
    ("a parse error leaves its scope alone", "bad = broken(s, e) or (sc.parent >= 0 and tainted[sc.parent])",
     "bad = False", "sec_facts"),
    ("a scope nested in a broken one is trusted", "bad = broken(s, e) or (sc.parent >= 0 and tainted[sc.parent])",
     "bad = broken(s, e)", "sec_facts"),
    ("a class holding a parse error is trusted", "                if broken(*self.class_ranges[ci]):\n"
     "                    cf.dynamic = True", "                if broken(*self.class_ranges[ci]):\n                    pass",
     "sec_facts"),
    ("indirect calls are not counted", "        if indirect:\n            self.calls.append((n.start_byte, indirect))",
     "        if False:\n            self.calls.append((n.start_byte, indirect))", "sec_facts"),
    ("a broken import resolves unbound names",
     '            if t in ("import_statement", "ERROR") or t in _DECLARATIONS:',
     '            if False:', "sec_facts"),
    ("`export *` ambiguity ignored", "if len(hits) == 1 and not ext and not blind:", "if hits:", "sec_binder"),
    ("`import * as` of a `module.exports = X` module binds", 'return ("mod", rel, "esm") if w is None else None',
     'return ("mod", rel, "esm")', "sec_binder"),
    # --- the phase-2 review
    ("a write through a type assertion is not a write", "        elif n.type in _CASTS:\n",
     "        elif False:\n", "sec_e2e:ts_casts"),
]


def _mutant(src: str, olds: list, news: list, tag: str):
    spec = importlib.util.spec_from_loader(f"receiver_facts_ts_mutant_{tag}", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = str(BUILD / "receiver_facts_ts.py")
    sys.modules[spec.name] = mod
    for old, new in zip(olds, news):
        src = src.replace(old, new, 1)
    exec(compile(src, str(BUILD / "receiver_facts_ts.py"), "exec"), mod.__dict__)
    return mod


def sec_mutations(rec: Rec, mod=None) -> None:
    src = (BUILD / "receiver_facts_ts.py").read_text(encoding="utf-8")
    for i, (why, old, new, section, *unless) in enumerate(MUTATIONS):
        if unless and core_has(unless[0]):
            print(f"  [SKIP] G mutation '{why}': the core's rule '{unless[0]}' covers it (the reader's stands down)")
            continue
        olds, news = ([old], [new]) if isinstance(old, str) else (list(old), list(new))
        if any(src.count(o) != 1 for o in olds):
            rec.check(f"G mutation '{why}': each anchor is in the module exactly once", False,
                      [src.count(o) for o in olds])
            continue
        try:
            m = _mutant(src, olds, news, str(i))
            quiet = Rec(loud=False)
            if section.startswith("sec_e2e"):
                only = section.partition(":")[2]
                sec_e2e(quiet, m, buckets=("0839",), trees=only.split(",") if only else _BASE_TREES)
            else:
                globals()[section](quiet, m)
            red, detail = quiet.failed > 0, quiet.fails[:2]
        except Exception as exc:  # noqa: BLE001 — a crash is RED too
            red, detail = True, repr(exc)
        rec.check(f"G mutation '{why}' turns {section} RED", red, detail)


# ================================================================================================
# --capture
# ================================================================================================

def _relative(graph: dict, root: Path) -> dict:
    prefixes = [str(root) + os.sep, str(root).replace("\\", "/") + "/"]

    def fix(v):
        if isinstance(v, str):
            for p in prefixes:
                if v.lower().startswith(p.lower()):
                    return v[len(p):].replace("\\", "/")
        return v

    return {k: ([{kk: fix(vv) for kk, vv in item.items()} if isinstance(item, dict) else item for item in v]
                if isinstance(v, list) else v) for k, v in graph.items()}


def do_capture() -> int:
    ver = GX.graphify_version()
    b = BUCKETS.get(ver)
    if b is None:
        print(f"--capture: graphify {ver} is not one of {sorted(BUCKETS)}")
        return 1
    for name in TREES:
        cur = load_capture(b, name)
        if cur is not None and cur.get("tree_sha256") == tree_sha(name) and cur.get("engine") == ver:
            print(f"--capture: {fixture_path(b, name).relative_to(REPO)} is current (kept)")
            continue
        root, graph, rep = real_graph(name)
        if graph is None:
            print(f"--capture: the engine failed on {name}: {(rep or {}).get('cause')}")
            return 1
        p = fixture_path(b, name)
        p.parent.mkdir(parents=True, exist_ok=True)
        doc = {"_about": (f"REAL `graphify update --no-cluster` output (graphify {ver}) of "
                          f"tests/typed_calls_ts_gates.TREES[{name!r}] (absolute paths made root-relative); "
                          "re-capture with `python tests/typed_calls_ts_gates.py --capture` under this engine."),
               "engine": ver, "tree_sha256": tree_sha(name), "graph": _relative(graph, root)}
        p.write_bytes((json.dumps(doc, indent=1, sort_keys=True) + "\n").encode("utf-8"))
        print(f"--capture: wrote {p.relative_to(REPO)} ({len(graph.get('nodes', []))} nodes, "
              f"{len(graph.get('links', []))} links)")
    return 0


# ================================================================================================

SECTIONS = [
    ("A: the module contract", sec_contract),
    ("B: the grammar canary", sec_canary),
    ("C: the IR the reader writes", sec_facts),
    ("D: the binder", sec_binder),
    ("E: every shape and must-not end to end, both engines' captures", sec_e2e),
    ("F: the installed engine", sec_real),
    ("H: shapes whose fix is in the core (static_split, PATCH_RULES loose / star)", sec_core),
    ("I: dynamic dispatch and patched builtin prototypes (both engines' captures)", sec_dispatch),
    ("K: an absent tree-sitter binding / grammar", sec_no_grammar),
    ("G: mutation teeth", sec_mutations),
]


def main() -> int:
    rec = Rec(loud=True)
    print(f"typed_calls TS/JS reader gates (S3c) — build/ at {BUILD}")
    try:
        missing = [n for n, m in (("typed_calls", TC), ("graphify_extract", GX), ("sync", S),
                                   ("typescript_edges", TE)) if m is None]
        if missing:
            rec.check("the build modules import", False, {n: _IMPORT_ERRORS.get(n) for n in missing})
        elif CAPTURE:
            return do_capture()
        else:
            sections = SECTIONS
            if R is None:                         # the pre-feature tree: every end-to-end check runs (and fails)
                rec.check("build/receiver_facts_ts.py imports", False, _IMPORT_ERRORS.get("receiver_facts_ts"))
                sections = [s for s in SECTIONS if s[1] in (sec_e2e, sec_real)]
            for title, fn in sections:
                print(f"\n-- {title} --")
                try:
                    fn(rec)
                except Exception:  # noqa: BLE001 — a crashing section is RED, never a traceback exit
                    import traceback
                    rec.check(f"{title}: section ran without crashing", False, traceback.format_exc()[-1500:])
    finally:
        cleanup()
    print(f"\nRESULT: {'GREEN' if rec.failed == 0 else 'RED'} — {rec.passed} passed, {rec.failed} failed")
    return 0 if rec.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
