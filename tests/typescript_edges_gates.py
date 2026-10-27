#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gates for the TypeScript / JavaScript recognizer (plugin #6, no Neo4j / tsc / Node runtime).

graphify extracts TypeScript and JavaScript at the node/call layer and persists no framework wiring, so a
NestJS controller, an Express router, a Next.js route handler or a TypeORM relation was invisible to
deepmap_impact. build/typescript_edges.py reads those declarations. These gates run a small app per
framework (APP below, written to a temp dir) through the REAL engine's extraction and the production seam
(sync._apply_recognizers -> edge_resolve.edge_rows) and lock:

  E   every expected edge, its direction, its resolved endpoints and its confidence, for NestJS (routes with
      the composed path + the global prefix of their own app — not a second app's, not an e2e test's, also
      from a bootstrap file in a directory of its own —, @Sse, a route on a class with no @Controller,
      guards / interceptors -> HOOKS, constructor / @InjectRepository / @Module registrations -> DEPENDS_ON,
      @Cron / @Interval / Bull @Process -> SCHEDULED_AS, @OnEvent / @EventPattern / @MessagePattern /
      @SubscribeMessage -> TRIGGERS), TypeORM (relations, onDelete and remove cascades in the existing
      direction, INHERITS; an entity named `Event`, a default export imported under another name), Express
      (named / imported / wrapped / class-method handlers, a module-level `new X()` instance's handler, route
      chains, middleware, a mount in another module composing the path while a test's mount does not, two
      module objects' same-named actions staying two edges, one handler through a renamed import and a
      namespace member folded beside a local function of that name, a template after a `>` comparison in a
      .js file), Fastify (route objects incl. a shorthand handler, a plugin registered with a prefix
      elsewhere), Node.js subpath imports (`#src/*`), koa-router (prefix, named routes, controller objects
      imported through a barrel, one of them a default-exported instance), Hono (basePath, sub-app mounts, a
      sub-app through a barrel, app.on), Next.js (app router route.ts + pages/api, a project root holding
      next.config), Angular (Routes + guards — one guard on two routes folded —, two aliased same-named
      components, constructor and inject() DI), a `log/` feature module with a guard through a tsconfig
      `paths` alias, and zod's interface + const companions (one row each, not a fan-out) — and NOTHING
      else: the resolved row set equals the expected set (plus the engine-version rows the gate names);
      tsc output, an npm scope's type and decoys named like the classes above get no edge;
  L   no edge from look-alikes: decorator / router text in strings, template literals and comments, `.get()`
      on a Map / axios / an axios instance, handlers that are variables, parameters or undeclared names, a
      private Next.js folder, a type / guard / base imported from an external package — including when the
      repo has its OWN class of that name;
  R   the recognizer alone (no engine): the TypedEdges it emits, conf hints, attrs, ensured nodes (named as the
      engine names them, `:Method` nodes for function-valued class properties), .d.ts / minified / bundled
      files skipped, framework-typed and class-based routers, paths folded from literal consts and fields;
      R4 the reader plus its finalize post-pass over small trees: only the same handler folds (a guard's
      `path` the first of its `paths`), the NestJS global prefix stays in its own app (a bootstrap file in a
      directory of its own included; never above its package, nor, with two or more apps, its Nx / workspace /
      Nest CLI project, while one app's prefix reaches its libraries — also in the layouts the Nx and Nest CLI
      generators write — but never another application project's routes), mounts through NodeNext `.js`
      specifiers / an alias / a barrel (an anonymous default plugin too, also one written `module.exports =
      exports = async function`) / koa allowedMethods / several operands / inline Fastify plugins, test mounts,
      Next.js project roots, @Sse / @Search, Angular `forChild(routes)`, and folding only a const in scope
      (never a parameter, another function's const, a reassigned field, a for-of / for-in variable, an
      uninitialized let / var or a later declarator; each declarator of a list keeps its own value);
      R5 the reader's precision: the lexer (a regex after an `if (...)` head, `x++ / 2`, a backtick in JSX
      text, and a template after a comparison, in a call, inside a template's ${ } or after a JSX closing tag's
      `</` that is never JSX text), compiled JavaScript skipped, imported classes named like DOM globals,
      MikroORM reference types and Collection initializers, which wrapped properties are handlers, nested
      helpers vs imports, `new X()`, `this` in an object literal after a class;
      R6 imports placed in the tree by finalize: tsconfig `paths` (and `extends`), `baseUrl` (before packages),
      Node.js subpath imports, workspace packages, unknown npm scopes, barrels (default-exported instances and
      object literals included), default exports, an imported handler beside a local function of its name,
      one handler through two import forms, a guessed alias, line-picked companions; a module object's member
      by what the module exports (a class, an instance, an object const, an ESM `export default { ... }`, a
      wrapped CommonJS export of its own function, `Object.assign(module.exports, ...)`, the legacy
      `module.exports = exports = { ... }`, a destructured `export const`, also with a default holding `:`),
      a class's or instance's member through its `extends` chain (a base in the module or imported, a class
      expression's; also on a named import's class or the router file's own `new X()` of it), a member the class
      sets itself (`this.x = ...` in its code, `X.x = ...` / `X.prototype.x = ...`) overriding the base's while a
      self-bind `this.x = this.x.bind(this)` keeps it, never a private function of the member's name;
  P   CASCADES persist REVERSED and impact(principal) lists the dependent; the registry entry is drift-locked
      (vocabulary ⊆ ALLOWED_EDGE_TYPES, traversable types already in retrieval's default walk — retrieval.py
      unchanged); DEEPMAP_RECOGNIZERS gates the entry; sync's vendor rule keeps node_modules/, dist/ out;
      P4 / P5 edge_resolve's TypeScript scopes (module, pref, exact, spec, a guessed alias, `#` notes);
      P6 the walk: feature modules under log/ build/ target/ read, node_modules/ never descended, one walk;
  S   linear cost (lines executed, no clock) on large generated files (class-based routers at a size where a
      per-lookup scan of every class would show); finalize's facts of one module (lines executed plus the
      characters its regexes scan) over a long chain of class-expression consts and many subclasses.

Engine coverage: section E runs on the CAPTURED graphify 0.8.39 output of APP
(tests/fixtures/typescript_frameworks/graph_0839.json, mapped with the same root, so the pass is the
production one) AND on the installed engine when there is one (0.8.51 in CI). A fixture that no longer
matches APP is a failure (re-capture it), never a silent pass.

Usage: python tests/typescript_edges_gates.py [--root <tree> | --root=<tree>]   (--root: run against another
       checkout's build/ — the RED-first run; a missing value or a tree without build/ is a usage error)
       python tests/typescript_edges_gates.py --capture   (re-capture the fixture after editing APP; needs
       graphify 0.8.39 installed, writes nothing unless the trimmed capture maps back to the live nodes)
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "typescript_frameworks" / "graph_0839.json"


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


_CAPTURE = __name__ == "__main__" and "--capture" in sys.argv[1:]
ROOT, _USAGE = (_parse_root([a for a in sys.argv[1:] if a != "--capture"]) if __name__ == "__main__"
                else (REPO, None))
if _USAGE:
    print(f"usage: python tests/typescript_edges_gates.py [--root <tree>] [--capture]  ({_USAGE})", file=sys.stderr)
    raise SystemExit(2)
BUILD = ROOT / "build"
sys.path.insert(0, str(BUILD))

import edge_resolve as ER          # noqa: E402
import framework_edges as FE       # noqa: E402
import graphify_extract as GX      # noqa: E402
import language_recognizers as LR  # noqa: E402
import sync as S                   # noqa: E402

try:
    import typescript_edges as T   # noqa: E402
except ImportError:                # the pre-feature tree: every gate below goes RED, none crashes
    T = None

_PASS = _FAIL = 0
_HAS_ENGINE = importlib.util.find_spec("graphify") is not None


def check(desc: str, cond: bool, detail="") -> None:
    global _PASS, _FAIL
    ok = bool(cond)
    _PASS += ok
    _FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc}" + ("" if ok else f"  -- {str(detail)[:900]}"))


@contextlib.contextmanager
def _env(**kv):
    saved = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


_FLAGS = dict(DEEPMAP_RECOGNIZERS=None, DEEPMAP_INCLUDE_VENDOR=None, DEEPMAP_EXCLUDE_TESTS=None,
              DEEPMAP_MERGE_SUMMARIES=None, DEEPMAP_PRECISE_CALLS=None, DEEPMAP_GRAPHIFY_CALLS_PLUS=None,
              DEEPMAP_RESOLVED_IMPORTS=None, DEEPMAP_EXTRACTOR=None, DEEPMAP_RESCUE_SKIPPED_DIRS=None,
              DEEPMAP_CASE_INSENSITIVE_EXT=None, DEEPMAP_SELF_CALL_REBIND=None, DEEPMAP_CLEAN_OWNER_NAMES=None)


# =====================================================================================================
# The fixture app: one small app per framework (+ look-alikes). Written to a temp dir at run time.
# =====================================================================================================
APP = {
    # ---------------------------------------------------------------- NestJS
    "nest/src/cats/cats.controller.ts": """\
import { Controller, Get, Post, Put, Patch, Delete, Sse, Body, Param, UseGuards, UseInterceptors, Inject } from '@nestjs/common';
import { AuthGuard } from '@nestjs/passport';
import { ConfigService } from '@nestjs/config';
import { CatsService } from './cats.service';
import { RolesGuard } from '../common/roles.guard';
import { LoggingInterceptor } from '../common/logging.interceptor';

@Controller('cats')
@UseGuards(RolesGuard)
export class CatsController {
  constructor(
    private readonly catsService: CatsService,
    private readonly config: ConfigService,
    @Inject('CACHE_TTL') private readonly ttl: number,
  ) {}

  @Get()
  findAll() {
    return this.catsService.findAll();
  }

  @Get(':id')
  @UseInterceptors(LoggingInterceptor)
  findOne(@Param('id') id: string) {
    return this.catsService.findOne(id);
  }

  @Post()
  @UseGuards(AuthGuard('jwt'))
  create(@Body() dto: unknown) {
    return this.catsService.create(dto);
  }

  @Put(':id')
  @Patch(':id')
  update(@Param('id') id: string, @Body() dto: unknown) {
    return this.catsService.update(id, dto);
  }

  // @Delete('purge')
  @Delete(':id')
  remove(@Param('id') id: string) {
    return this.catsService.remove(id);
  }

  @Sse('events')
  events() {
    return this.catsService.findAll();
  }
}
""",
    "nest/src/cats/cats.service.ts": """\
import { Injectable } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { Cron, CronExpression, Interval } from '@nestjs/schedule';
import { OnEvent, EventEmitter2 } from '@nestjs/event-emitter';
import { Cat } from './cat.entity';

@Injectable()
export class CatsService {
  constructor(
    @InjectRepository(Cat) private readonly cats: Repository<Cat>,
    private readonly events: EventEmitter2,
  ) {}

  findAll() { return this.cats.find(); }
  findOne(id: string) { return this.cats.findOneBy({ id }); }
  create(dto: unknown) { return this.cats.save(dto as Cat); }
  update(id: string, dto: unknown) { return this.cats.update(id, dto as Cat); }
  remove(id: string) { return this.cats.delete(id); }

  @Cron(CronExpression.EVERY_HOUR)
  purgeStale() { return this.cats.delete({}); }

  @Interval(30000)
  heartbeat() { return 1; }

  @OnEvent('cat.created')
  onCatCreated(payload: unknown) { return payload; }
}
""",
    "nest/src/cats/cats.module.ts": """\
import { Module, NestModule, MiddlewareConsumer, RequestMethod } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { CatsController } from './cats.controller';
import { CatsService } from './cats.service';
import { Cat } from './cat.entity';
import { LoggerMiddleware } from '../common/logger.middleware';

@Module({
  imports: [TypeOrmModule.forFeature([Cat])],
  controllers: [CatsController],
  providers: [CatsService],
  exports: [CatsService],
})
export class CatsModule implements NestModule {
  configure(consumer: MiddlewareConsumer) {
    consumer
      .apply(LoggerMiddleware)
      .exclude({ path: 'cats/health', method: RequestMethod.GET })
      .forRoutes(CatsController, { path: 'cats/*', method: RequestMethod.POST });
  }
}
""",
    "nest/src/common/logger.middleware.ts": """\
import { Injectable, NestMiddleware } from '@nestjs/common';

@Injectable()
export class LoggerMiddleware implements NestMiddleware {
  use(req: unknown, res: unknown, next: () => void) {
    next();
  }
}
""",
    "nest/src/app.module.ts": """\
import { Module, forwardRef } from '@nestjs/common';
import { APP_GUARD } from '@nestjs/core';
import { CatsModule } from './cats/cats.module';
import { OrdersModule } from './orders/orders.module';
import { RolesGuard } from './common/roles.guard';

@Module({
  imports: [CatsModule, forwardRef(() => OrdersModule)],
  providers: [{ provide: APP_GUARD, useClass: RolesGuard }],
})
export class AppModule {}
""",
    "nest/src/main.ts": """\
import { NestFactory } from '@nestjs/core';
import { AppModule } from './app.module';

async function bootstrap() {
  const app = await NestFactory.create(AppModule);
  app.setGlobalPrefix('api');
  app.get('/not-a-route', bootstrap);
  await app.listen(3000);
}
bootstrap();
""",
    # An e2e test sets its own prefix: test files never contribute a global prefix.
    "nest/test/app.e2e-spec.ts": """\
import { Test } from '@nestjs/testing';
import { AppModule } from '../src/app.module';

describe('AppController (e2e)', () => {
  it('serves under a test prefix', async () => {
    const moduleRef = await Test.createTestingModule({ imports: [AppModule] }).compile();
    const app = moduleRef.createNestApplication();
    app.setGlobalPrefix('v9');
    await app.init();
  });
});
""",
    # A second Nest app with no global prefix: the first app's prefix stays in its own directory.
    "nest2/src/main.ts": """\
import { NestFactory } from '@nestjs/core';
import { WorkerModule } from './worker.module';

async function bootstrap() {
  const app = await NestFactory.create(WorkerModule);
  await app.listen(3001);
}
bootstrap();
""",
    "nest2/src/jobs.controller.ts": """\
import { Controller, Post } from '@nestjs/common';

@Controller('jobs')
export class JobsController {
  @Post()
  enqueue() {
    return true;
  }
}
""",
    # A third Nest app whose bootstrap file sits in a directory of its own: its prefix reaches src/modules/.
    "nest3/src/bootstrap/main.ts": """\
import { NestFactory } from '@nestjs/core';
import { HealthModule } from '../health.module';

async function bootstrap() {
  const app = await NestFactory.create(HealthModule);
  app.setGlobalPrefix('v3');
  await app.listen(3002);
}
bootstrap();
""",
    "nest3/src/modules/health.controller.ts": """\
import { Controller, Get } from '@nestjs/common';

@Controller('health')
export class HealthController {
  @Get()
  check() {
    return 'ok';
  }
}
""",
    # Route decorators on a class with no @Controller: served under each subclass's prefix, not its own.
    "nest/src/common/crud.controller.ts": """\
import { Get } from '@nestjs/common';

export abstract class CrudController {
  @Get(':id')
  findOne(id: string) {
    return id;
  }
}
""",
    "nest/src/dogs/dogs.controller.ts": """\
import { Controller } from '@nestjs/common';
import { CrudController } from '../common/crud.controller';

@Controller('dogs')
export class DogsController extends CrudController {}
""",
    # A feature module named `log` (read like any other) whose guard comes through a tsconfig `paths` alias.
    "nest/tsconfig.json": """\
{
  "compilerOptions": {
    "baseUrl": "./",
    "paths": { "@common/*": ["src/common/*"] }, // the Nest app's alias
  },
}
""",
    "nest/src/log/log.controller.ts": """\
import { Controller, Get, UseGuards } from '@nestjs/common';
import { RolesGuard } from '@common/roles.guard';

@Controller('log')
@UseGuards(RolesGuard)
export class LogController {
  @Get()
  tail() {
    return [];
  }
}
""",
    # An entity named like a DOM global, and a default export imported under another name (a decoy shares it).
    "nest/src/events/event.entity.ts": """\
import { Entity, PrimaryGeneratedColumn } from 'typeorm';

@Entity()
export class Event {
  @PrimaryGeneratedColumn()
  id: number;
}
""",
    "nest/src/events/organizer.entity.ts": """\
import { Entity, PrimaryGeneratedColumn } from 'typeorm';

@Entity()
export default class OrganizerEntity {
  @PrimaryGeneratedColumn()
  id: number;
}
""",
    "nest/src/events/ticket.entity.ts": """\
import { Entity, ManyToOne } from 'typeorm';
import { Event } from './event.entity';
import Organizer from './organizer.entity';

@Entity()
export class Ticket {
  @ManyToOne(() => Event, (event) => event.tickets, { onDelete: 'CASCADE' })
  event: Event;

  @ManyToOne(() => Organizer)
  organizer: Organizer;
}
""",
    "nest/src/common/roles.guard.ts": """\
import { Injectable, CanActivate } from '@nestjs/common';

@Injectable()
export class RolesGuard implements CanActivate {
  canActivate(): boolean {
    return true;
  }
}
""",
    "nest/src/common/logging.interceptor.ts": """\
import { Injectable, NestInterceptor } from '@nestjs/common';

@Injectable()
export class LoggingInterceptor implements NestInterceptor {
  intercept(context: unknown, next: unknown) {
    return next;
  }
}
""",
    # The repo's OWN class named like the external @nestjs/config one: it must never receive an edge.
    "nest/src/config/config.service.ts": """\
export class ConfigService {
  get(key: string) {
    return key;
  }
}
""",
    "nest/src/orders/orders.module.ts": """\
import { Module } from '@nestjs/common';
import { OrdersEvents } from './orders.events';
import { AudioConsumer } from './audio.consumer';
import { EventsGateway } from './events.gateway';

@Module({ controllers: [OrdersEvents], providers: [AudioConsumer, EventsGateway] })
export class OrdersModule {}
""",
    "nest/src/orders/orders.events.ts": """\
import { Controller } from '@nestjs/common';
import { EventPattern, MessagePattern, Payload } from '@nestjs/microservices';

@Controller()
export class OrdersEvents {
  @EventPattern('order_created')
  handleOrderCreated(@Payload() data: unknown) {
    return data;
  }

  @MessagePattern({ cmd: 'sum' })
  accumulate(data: number[]) {
    return data;
  }
}
""",
    "nest/src/orders/audio.consumer.ts": """\
import { Processor, Process } from '@nestjs/bull';

@Processor('audio')
export class AudioConsumer {
  @Process('transcode')
  async transcode(job: unknown) {
    return job;
  }
}
""",
    "nest/src/orders/events.gateway.ts": """\
import { WebSocketGateway, SubscribeMessage } from '@nestjs/websockets';

@WebSocketGateway()
export class EventsGateway {
  @SubscribeMessage('events')
  handleEvent(data: string) {
    return data;
  }
}
""",
    # ---------------------------------------------------------------- TypeORM
    "nest/src/cats/cat.entity.ts": """\
import { Entity, PrimaryGeneratedColumn, Column, ManyToOne, OneToMany, OneToOne, JoinColumn, ManyToMany, JoinTable, BeforeInsert, AfterLoad } from 'typeorm';
import { Owner } from '../owners/owner.entity';
import { Toy } from './toy.entity';
import { Profile } from './profile.entity';
import { Tag } from './tag.entity';
import { AuditedEntity } from '../common/audited.entity';

@Entity()
export class Cat extends AuditedEntity {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column()
  name: string;

  @ManyToOne(() => Owner, (owner) => owner.cats, { onDelete: 'CASCADE' })
  owner: Owner;

  @OneToMany(() => Toy, (toy) => toy.cat, { cascade: ['insert', 'remove'] })
  toys: Toy[];

  @OneToOne(() => Profile, { onDelete: 'SET NULL' })
  @JoinColumn()
  profile: Profile;

  @ManyToMany(() => Tag, { cascade: ['insert', 'update'] })
  @JoinTable()
  tags: Tag[];

  @BeforeInsert()
  normalizeName() {
    this.name = this.name.trim();
  }

  @AfterLoad()
  touch() {
    return this;
  }
}
""",
    "nest/src/owners/owner.entity.ts": """\
import { Entity, PrimaryGeneratedColumn, OneToMany, OneToOne } from 'typeorm';
import { Cat } from '../cats/cat.entity';
import { Address } from './index';

@Entity()
export class Owner {
  @PrimaryGeneratedColumn()
  id: number;

  @OneToMany(() => Cat, (cat) => cat.owner)
  cats: Cat[];

  @OneToOne(() => Address, (address) => address.owner, { onDelete: 'CASCADE' })
  address: Address;
}
""",
    "nest/src/owners/address.entity.ts": """\
import { Entity, PrimaryGeneratedColumn } from 'typeorm';

@Entity()
export class Address {
  @PrimaryGeneratedColumn()
  id: number;
}
""",
    # A barrel: the module an import names defines nothing itself -> the global name rule applies.
    "nest/src/owners/index.ts": """\
export * from './address.entity';
""",
    # Decoys: same class names in another directory nobody imports. The import decides which one binds.
    "nest/src/legacy/cats.service.ts": """\
export class CatsService {
  findAll() {
    return [];
  }
}
""",
    "nest/src/legacy/owner.ts": """\
export class Owner {
  name = 'legacy';
}
""",
    "nest/src/cats/toy.entity.ts": """\
import { Entity, PrimaryGeneratedColumn } from 'typeorm';

@Entity()
export class Toy {
  @PrimaryGeneratedColumn()
  id: number;
}
""",
    "nest/src/cats/profile.entity.ts": """\
import { Entity, PrimaryGeneratedColumn } from 'typeorm';

@Entity()
export class Profile {
  @PrimaryGeneratedColumn()
  id: number;
}
""",
    "nest/src/cats/tag.entity.ts": """\
import { Entity, PrimaryGeneratedColumn } from 'typeorm';

@Entity()
export class Tag {
  @PrimaryGeneratedColumn()
  id: number;
}
""",
    "nest/src/common/audited.entity.ts": """\
import { BaseEntity, CreateDateColumn } from 'typeorm';

export abstract class AuditedEntity extends BaseEntity {
  @CreateDateColumn()
  createdAt: Date;
}
""",
    # ---------------------------------------------------------------- Express
    "express/server.js": """\
const express = require('express');
const apiRouter = require('./routes');
const { requireAuth } = require('./middleware/auth');

const app = express();

function health(req, res) {
  res.send('ok');
}

app.get('/health', health);
app.use('/api', requireAuth, apiRouter);
app.listen(3000);

module.exports = app;
""",
    "express/routes/index.js": """\
const express = require('express');
const usersRouter = require('./users');

const router = express.Router();

const mounted = [
  { path: '/users', route: usersRouter },
];

mounted.forEach((entry) => {
  router.use(entry.path, entry.route);
});

module.exports = router;
""",
    "express/routes/users.js": """\
const express = require('express');
const controller = require('../controllers/users');
const { validate } = require('../middleware/validate');
const asyncHandler = require('express-async-handler');

const router = express.Router();
const notAFunction = buildHandler();

function listUsers(req, res) {
  res.json([]);
}

async function createUser(req, res) {
  res.status(201).json({});
}

router.get('/', listUsers);
router.post('/', validate, asyncHandler(createUser));
router.route('/:id').get(getUser).put(updateUser).patch(updateUser);
router.delete('/:id', controller.remove);
router.get('/ghost', ghostHandler);
router.get('/variable', notAFunction);

function getUser(req, res) {
  res.json({});
}

function updateUser(req, res) {
  res.json({});
}

function buildHandler() {
  return null;
}

// router.get('/disabled', buildHandler);
const help = "router.get('/fake', buildHandler)";
const tpl = `router.post('/tpl', ${'x'}, buildHandler)`;
const routeRx = /router.get('', buildHandler)/;

function proxy(client) {
  return client.get('/upstream', buildHandler);
}

module.exports = router;
""",
    "express/controllers/users.js": """\
exports.remove = function remove(req, res) {
  res.status(204).end();
};
""",
    "express/middleware/auth.js": """\
function requireAuth(req, res, next) {
  next();
}

module.exports = { requireAuth };
""",
    "express/middleware/validate.js": """\
function validate(req, res, next) {
  next();
}

module.exports = { validate };
""",
    "express/controllers/orders.controller.ts": """\
import { Router } from 'express';

export class OrdersController {
  public router = Router();

  constructor() {
    this.router.get('/orders', this.list);
    this.router.post('/orders', this.create.bind(this));
  }

  list(req: unknown, res: unknown) {
    return res;
  }

  create(req: unknown, res: unknown) {
    return res;
  }
}
""",
    "express/routes/products.route.ts": """\
import { Router } from 'express';
import { ProductsController } from '../controllers/products.controller';
import { requireAuth } from '../middleware/auth';

const API = '/v2';

export class ProductsRoute {
  public path = '/products';
  public router: Router = Router();
  private readonly products = new ProductsController();

  constructor() {
    this.router.get(`${this.path}`, this.products.list);
    this.router.get(`${API}${this.path}/:id`, requireAuth, this.products.getOne);
    this.router.post(this.path, this.products.create);
  }
}
""",
    "express/controllers/products.controller.ts": """\
import { asyncHandler } from '../middleware/async';

export class ProductsController {
  list = async (req: unknown, res: unknown) => {
    return res;
  };

  getOne = asyncHandler(async (req: unknown, res: unknown) => {
    return res;
  });

  create(req: unknown, res: unknown) {
    return res;
  }
}
""",
    "express/middleware/async.ts": """\
export const asyncHandler = (fn: unknown) => fn;
""",
    # Two module objects with the same action name: two handlers, two routes (never folded into one).
    "express/routes/mvc.ts": """\
import { Router } from 'express';
import * as articles from '../controllers/articles';
import * as people from '../controllers/people';

const router = Router();

router.get('/articles/:id', articles.show);
router.get('/people/:id', people.show);

export default router;
""",
    "express/controllers/articles.ts": """\
export function show(req: unknown, res: unknown) {
  return res;
}
""",
    "express/controllers/people.ts": """\
export function show(req: unknown, res: unknown) {
  return res;
}
""",
    # A handler on a module-level instance (`new LegacyController()`): its class's import names the module.
    "express/routes/legacy.ts": """\
import { Router } from 'express';
import { LegacyController } from '../controllers/legacy.controller';

const router = Router();
const legacy = new LegacyController();

router.get('/legacy', legacy.list);

export default router;
""",
    "express/controllers/legacy.controller.ts": """\
export class LegacyController {
  list(req: unknown, res: unknown) {
    return res;
  }
}
""",
    # tsc output of a class router: a copy of source code, never read.
    "express/lib/compiled.js": """\
"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.CompiledRoutes = void 0;
const express_1 = require("express");
class CompiledRoutes {
    constructor() {
        this.router = (0, express_1.Router)();
        this.router.get('/compiled', this.list);
    }
    list(req, res) { return res; }
}
exports.CompiledRoutes = CompiledRoutes;
//# sourceMappingURL=compiled.js.map
""",
    # A template holding HTML after a `>` comparison in a .js file: a template, never JSX text.
    "express/routes/badges.js": """\
const express = require('express');

const router = express.Router();

function listBadges(req, res) {
  const label = req.query.n > 0 ? `${req.query.n} badges</span>` : '';
  res.send(label);
}

router.get('/badges', listBadges);

const help = `router.get('/fake', listBadges)`;

module.exports = router;
""",
    # One handler through two import forms (a renamed named import, a namespace member) beside a local
    # function of the imported name: the imported one binds its module, the local one stays local.
    "express/routes/forms.ts": """\
import { Router } from 'express';
import { show as showArticle } from '../controllers/articles';
import * as articles from '../controllers/articles';

const router = Router();

function show(req: unknown, res: unknown) {
  return res;
}

router.get('/a/:id', showArticle);
router.get('/b/:id', articles.show);
router.get('/local', show);

export default router;
""",
    # A test mounts the users router at the root: production's /api/users stays the route's path.
    "express/test/users.test.js": """\
const express = require('express');
const usersRouter = require('../routes/users');

const app = express();
app.use(usersRouter);

module.exports = app;
""",
    # ---------------------------------------------------------------- Fastify
    "fastify/server.ts": """\
import Fastify from 'fastify';
import itemRoutes from './routes/items';
import { authenticate } from './hooks/auth';

const app = Fastify({ logger: true });

async function getStatus() {
  return { ok: true };
}

async function ping() {
  return 'pong';
}

async function handler() {
  return { ready: true };
}

app.get('/status', getStatus);
app.route({ method: ['GET', 'HEAD'], url: '/ping', preHandler: [authenticate], handler: ping });
app.route({ method: 'GET', url: '/ready', handler });
app.register(itemRoutes, { prefix: '/items' });

export default app;
""",
    "fastify/routes/items.ts": """\
import { FastifyInstance } from 'fastify';

async function listItems() {
  return [];
}

async function getItem() {
  return {};
}

export default async function itemRoutes(fastify: FastifyInstance) {
  fastify.get('/', listItems);
  fastify.get('/:id', { schema: {} }, getItem);
}
""",
    "fastify/hooks/auth.ts": """\
export async function authenticate() {
  return true;
}
""",
    # Node.js subpath imports (package.json `imports`, NodeNext `.js` names): a module-object handler and a base
    # class placed in the package's own src/ — the decoys of the same names in misc/ get nothing.
    "subpath/package.json": """\
{ "name": "subpath-app", "type": "module", "imports": { "#src/*": "./src/*" } }
""",
    "subpath/src/controllers/users.ts": """\
export async function listUsers(req: unknown, reply: unknown) {
  return reply;
}
""",
    "subpath/src/shared/base.repo.ts": """\
export class BaseRepo {}
""",
    "subpath/src/repos/users.repo.ts": """\
import { BaseRepo } from '#src/shared/base.repo.js';

export class UsersRepo extends BaseRepo {}
""",
    "subpath/src/routes.ts": """\
import express from 'express';
import * as users from '#src/controllers/users.js';

const router = express.Router();

router.get('/users', users.listUsers);

export default router;
""",
    # ---------------------------------------------------------------- koa-router
    "koa/books.js": """\
const Koa = require('koa');
const Router = require('@koa/router');

const app = new Koa();
const router = new Router({ prefix: '/v1' });

async function listBooks(ctx) {
  ctx.body = [];
}

async function removeBook(ctx) {
  ctx.status = 204;
}

router.get('books', '/books', listBooks);
router.del('/books/:id', removeBook);
app.use(router.routes());
""",
    # Controller objects imported through a barrel (`export { default as user } from './user'`): static handlers.
    "koa2/src/controller/index.ts": """\
export { default as general } from './general';
export { default as user } from './user';
export { default as status } from './status';
""",
    # A barrel's module that default-exports an INSTANCE: its object's handler is its class's method.
    "koa2/src/controller/status.ts": """\
import { Context } from 'koa';

class StatusController {
  async show(ctx: Context): Promise<void> {
    ctx.body = 'ok';
  }
}

export default new StatusController();
""",
    "koa2/src/controller/general.ts": """\
import { Context } from 'koa';

export default class GeneralController {
  public static async helloWorld(ctx: Context): Promise<void> {
    ctx.body = 'hello';
  }
}
""",
    "koa2/src/controller/user.ts": """\
import { Context } from 'koa';

export default class UserController {
  public static async getUsers(ctx: Context): Promise<void> {
    ctx.body = [];
  }
}
""",
    "koa2/src/routes.ts": """\
import Router from '@koa/router';
import { general, user, status } from './controller';

export const router = new Router();

router.get('/', general.helloWorld);
router.get('/users', user.getUsers);
router.get('/status', status.show);
""",
    # ---------------------------------------------------------------- Hono
    "hono/index.ts": """\
import { Hono } from 'hono';
import { listPosts } from './posts';
import { authors } from './routes';

const api = new Hono().basePath('/api');
const books = new Hono();

function getBook(c: any) {
  return c.json({});
}

function updatePost(c: any) {
  return c.text('ok');
}

books.get('/:id', getBook);
api.get('/posts', listPosts);
api.on(['PUT', 'DELETE'], '/posts/:id', updatePost);
api.route('/books', books);
api.route('/authors', authors);

export default api;
""",
    # A sub-app mounted through a barrel (`export { default as authors } from './authors'`).
    "hono/routes/index.ts": """\
export { default as authors } from './authors';
""",
    "hono/routes/authors.ts": """\
import { Hono } from 'hono';

const authors = new Hono();

function listAuthors(c: any) {
  return c.json([]);
}

authors.get('/', listAuthors);

export default authors;
""",
    "hono/posts.ts": """\
export async function listPosts(c: any) {
  return c.json([]);
}
""",
    # ---------------------------------------------------------------- Next.js
    "web/app/api/users/[id]/route.ts": """\
import { NextResponse } from 'next/server';

export async function GET(request: Request) {
  return NextResponse.json({ id: 1 });
}

export async function DELETE() {
  return new Response(null, { status: 204 });
}

export const PATCH = async (request: Request) => {
  return NextResponse.json({});
};
""",
    "web/app/(marketing)/api/health/route.ts": """\
export function GET() {
  return Response.json({ ok: true });
}
""",
    "web/app/docs/[...slug]/route.ts": """\
export async function GET() {
  return new Response('doc');
}
""",
    "web/app/_lib/internal/route.ts": """\
export async function GET() {
  return new Response('private folders are not routable');
}
""",
    "web/pages/api/items/[itemId].ts": """\
import type { NextApiRequest, NextApiResponse } from 'next';

export default function handler(req: NextApiRequest, res: NextApiResponse) {
  res.status(200).json({});
}
""",
    "web/pages/api/legacy/index.js": """\
function legacyHandler(req, res) {
  res.json({});
}

export default legacyHandler;
""",
    # A Turborepo-style app whose package directory is itself named `app` (next-forge's apps/app): the
    # app router is the `app` directory next to next.config, not the first `app` in the path.
    "forge/apps/app/next.config.ts": """\
const nextConfig = { reactStrictMode: true };

export default nextConfig;
""",
    "forge/apps/app/app/api/collab/route.ts": """\
export async function POST(request: Request) {
  return Response.json({ ok: true });
}
""",
    # ---------------------------------------------------------------- Angular
    "ng/src/app/app.routes.ts": """\
import { Routes } from '@angular/router';
import { HeroesComponent } from './heroes/heroes.component';
import { HeroDetailComponent } from './heroes/hero-detail.component';
import { ListComponent as UsersList } from './users/list.component';
import { ListComponent as OrdersList } from './orders/list.component';
import { authGuard } from './auth.guard';

export const routes: Routes = [
  { path: 'heroes', component: HeroesComponent, canActivate: [authGuard] },
  { path: 'admin', children: [{ path: 'detail/:id', component: HeroDetailComponent }] },
  { path: 'users', component: UsersList, canActivate: [authGuard] },
  { path: 'orders', component: OrdersList },
  { path: '', redirectTo: '/heroes', pathMatch: 'full' },
];
""",
    # Two components exported under one name from two modules (imported under aliases).
    "ng/src/app/users/list.component.ts": """\
import { Component } from '@angular/core';

@Component({ selector: 'app-users-list', template: '<p>users</p>' })
export class ListComponent {}
""",
    "ng/src/app/orders/list.component.ts": """\
import { Component } from '@angular/core';

@Component({ selector: 'app-orders-list', template: '<p>orders</p>' })
export class ListComponent {}
""",
    "ng/src/app/auth.guard.ts": """\
export function authGuard() {
  return true;
}
""",
    "ng/src/app/heroes/heroes.component.ts": """\
import { Component, inject } from '@angular/core';
import { HeroService } from '../hero.service';
import { MessageService } from '../message.service';

@Component({ selector: 'app-heroes', templateUrl: './heroes.component.html' })
export class HeroesComponent {
  private messages = inject(MessageService);

  constructor(private heroService: HeroService) {}

  load() {
    return this.heroService.all();
  }
}
""",
    "ng/src/app/heroes/hero-detail.component.ts": """\
import { Component } from '@angular/core';

@Component({ selector: 'app-hero-detail', template: '<p>detail</p>' })
export class HeroDetailComponent {}
""",
    "ng/src/app/hero.service.ts": """\
import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';

@Injectable({ providedIn: 'root' })
export class HeroService {
  constructor(private http: HttpClient) {}

  all() {
    return this.http.get('/api/heroes');
  }
}
""",
    "ng/src/app/message.service.ts": """\
import { Injectable } from '@angular/core';

@Injectable({ providedIn: 'root' })
export class MessageService {
  add(message: string) {
    return message;
  }
}
""",
    # ---------------------------------------------------------------- look-alikes (no edge may come from here)
    "misc/lookalikes.ts": """\
import axios from 'axios';

const cache = new Map<string, string>();
const client = axios.create({ baseURL: '/api' });

function onUsers(res: unknown) {
  return res;
}

export async function load() {
  cache.get('/users');
  await axios.get('/users', onUsers);
  await client.get('/users', onUsers);
  $.get('/api/users', onUsers);
  const doc = `
    @Controller('fake')
    class Fake { @Get('/x') x() {} }
    router.get('/fake', onUsers);
  `;
  // router.get('/commented', onUsers);
  /* @Get('/block') */
  return doc + "@Post('/str') app.get('/s', onUsers)";
}
""",
    "misc/widget.tsx": """\
import React from 'react';

export function Widget() {
  const label = "it's a router.get('/x', onUsers) string";
  return <a href="/docs">Don't use router.get('/y', h) here // nor here</a>;
}
""",
    # A type from an npm scope the tree holds no package for, beside the repo's own class of that name.
    "misc/post.ts": """\
import { Model } from '@vuex-orm/core';

export class Post extends Model {}
""",
    "misc/model.ts": """\
export class Model {}
""",
    # Decoys named like the module-level instance's class and the renamed default export.
    "misc/legacy.controller.ts": """\
export class LegacyController {
  list() {
    return [];
  }
}
""",
    "misc/organizer.ts": """\
export class Organizer {}
""",
    # Decoys named like the subpath app's base class and handler.
    "misc/base.repo.ts": """\
export class BaseRepo {}
""",
    "misc/users.ts": """\
export async function listUsers() {
  return [];
}
""",
    # zod v4's shape: an interface and a `const` of the same name, both node-ified by the engine.
    "zod/schemas.ts": """\
export interface ZodType {
  _def: unknown;
}
export const ZodType: core.$constructor<ZodType> = core.$constructor('ZodType', (inst, def) => {});

export interface ZodString extends ZodType {
  minLength: number;
}
export const ZodString: core.$constructor<ZodString> = core.$constructor('ZodString', (inst, def) => {});
""",
}

# Files whose content must yield no typed edge at all.
LOOKALIKE_FILES = {"misc/lookalikes.ts", "misc/widget.tsx", "nest/src/config/config.service.ts",
                   "web/app/_lib/internal/route.ts", "nest/src/main.ts", "nest/src/legacy/cats.service.ts",
                   "nest/src/legacy/owner.ts", "nest/src/owners/index.ts", "nest/test/app.e2e-spec.ts",
                   "nest2/src/main.ts", "express/test/users.test.js", "forge/apps/app/next.config.ts",
                   "express/lib/compiled.js", "koa2/src/controller/index.ts", "misc/post.ts", "misc/model.ts",
                   "misc/legacy.controller.ts", "misc/organizer.ts", "misc/base.repo.ts", "misc/users.ts",
                   "hono/routes/index.ts", "nest3/src/bootstrap/main.ts"}
# The `prefix_unresolved` of a route declared on a class with no @Controller (each subclass's prefix applies).
NO_CONTROLLER = "<no @Controller: the prefix of each subclass>"

N = "nest/src/"
# The resolved rows: (edge type, (src name, src file), (dst name, dst file), confidence, attrs that must match).
# A CASCADES row is as edge_resolve resolves it: (principal) then (dependent); it persists dependent -> principal.
EXPECTED = [
    # NestJS routes: composed path + the single app.setGlobalPrefix('api') (finalize)
    ("ROUTES_TO", ("CatsController", N + "cats/cats.controller.ts"), ("CatsController.findAll", N + "cats/cats.controller.ts"),
     "EXTRACTED", {"method": "GET", "path": "/api/cats", "framework": "nestjs", "global_prefix": "api"}),
    ("ROUTES_TO", ("CatsController", N + "cats/cats.controller.ts"), ("CatsController.findOne", N + "cats/cats.controller.ts"),
     "EXTRACTED", {"method": "GET", "path": "/api/cats/:id"}),
    ("ROUTES_TO", ("CatsController", N + "cats/cats.controller.ts"), ("CatsController.create", N + "cats/cats.controller.ts"),
     "EXTRACTED", {"method": "POST", "path": "/api/cats"}),
    ("ROUTES_TO", ("CatsController", N + "cats/cats.controller.ts"), ("CatsController.update", N + "cats/cats.controller.ts"),
     "EXTRACTED", {"method": "PATCH,PUT", "path": "/api/cats/:id"}),
    ("ROUTES_TO", ("CatsController", N + "cats/cats.controller.ts"), ("CatsController.remove", N + "cats/cats.controller.ts"),
     "EXTRACTED", {"method": "DELETE", "path": "/api/cats/:id"}),
    # @Sse is a GET route (NestJS server-sent events)
    ("ROUTES_TO", ("CatsController", N + "cats/cats.controller.ts"), ("CatsController.events", N + "cats/cats.controller.ts"),
     "EXTRACTED", {"method": "GET", "path": "/api/cats/events", "global_prefix": "api"}),
    # a second Nest app: main.ts's prefix is its own app's (nest/src), and an e2e test's prefix counts nowhere
    ("ROUTES_TO", ("JobsController", "nest2/src/jobs.controller.ts"), ("JobsController.enqueue", "nest2/src/jobs.controller.ts"),
     "EXTRACTED", {"method": "POST", "path": "/jobs", "global_prefix": None}),
    # a bootstrap file in a directory of its own (nest3/src/bootstrap/main.ts): its prefix reaches the app's routes
    ("ROUTES_TO", ("HealthController", "nest3/src/modules/health.controller.ts"),
     ("HealthController.check", "nest3/src/modules/health.controller.ts"), "EXTRACTED",
     {"method": "GET", "path": "/v3/health", "framework": "nestjs", "global_prefix": "v3"}),
    # a route decorator on a class with no @Controller: the path is the method's own, the prefix is unknown
    # (each subclass's) and no global prefix is guessed
    ("ROUTES_TO", ("CrudController", N + "common/crud.controller.ts"), ("CrudController.findOne", N + "common/crud.controller.ts"),
     "EXTRACTED", {"method": "GET", "path": "/:id", "prefix_unresolved": NO_CONTROLLER, "global_prefix": None}),
    ("INHERITS", ("DogsController", N + "dogs/dogs.controller.ts"), ("CrudController", N + "common/crud.controller.ts"),
     "INFERRED", {}),
    # a feature module named `log` is read; its guard comes through the app's tsconfig `paths` alias
    ("ROUTES_TO", ("LogController", N + "log/log.controller.ts"), ("LogController.tail", N + "log/log.controller.ts"),
     "EXTRACTED", {"method": "GET", "path": "/api/log", "global_prefix": "api"}),
    ("HOOKS", ("LogController", N + "log/log.controller.ts"), ("RolesGuard", N + "common/roles.guard.ts"),
     "INFERRED", {"event": "guard"}),
    # NestJS lifecycle hooks: class-level guard (src = the class), handler-level interceptor (src = the method)
    ("HOOKS", ("CatsController", N + "cats/cats.controller.ts"), ("RolesGuard", N + "common/roles.guard.ts"),
     "INFERRED", {"event": "guard"}),
    ("HOOKS", ("CatsController.findOne", N + "cats/cats.controller.ts"),
     ("LoggingInterceptor", N + "common/logging.interceptor.ts"), "INFERRED", {"event": "interceptor"}),
    # NestJS module middleware: consumer.apply(...).exclude(...).forRoutes(...) in configure()
    ("HOOKS", ("CatsModule", N + "cats/cats.module.ts"), ("LoggerMiddleware", N + "common/logger.middleware.ts"),
     "INFERRED", {"event": "middleware", "routes": ["CatsController", "cats/* POST"]}),
    # NestJS DI: constructor injection, @InjectRepository(entity), @Module registrations
    ("DEPENDS_ON", ("CatsController", N + "cats/cats.controller.ts"), ("CatsService", N + "cats/cats.service.ts"),
     "INFERRED", {"via": "ctor", "param": "catsService"}),
    ("DEPENDS_ON", ("CatsService", N + "cats/cats.service.ts"), ("Cat", N + "cats/cat.entity.ts"),
     "INFERRED", {"via": "ctor", "param": "cats", "inject": "InjectRepository"}),
    ("DEPENDS_ON", ("CatsModule", N + "cats/cats.module.ts"), ("CatsController", N + "cats/cats.controller.ts"),
     "INFERRED", {"via": "module", "role": "controllers"}),
    ("DEPENDS_ON", ("CatsModule", N + "cats/cats.module.ts"), ("CatsService", N + "cats/cats.service.ts"),
     "INFERRED", {"via": "module", "role": "providers"}),
    ("DEPENDS_ON", ("AppModule", N + "app.module.ts"), ("CatsModule", N + "cats/cats.module.ts"),
     "INFERRED", {"via": "module", "role": "imports"}),
    ("DEPENDS_ON", ("AppModule", N + "app.module.ts"), ("OrdersModule", N + "orders/orders.module.ts"),
     "INFERRED", {"via": "module", "role": "imports"}),
    ("DEPENDS_ON", ("AppModule", N + "app.module.ts"), ("RolesGuard", N + "common/roles.guard.ts"),
     "INFERRED", {"via": "module", "role": "providers", "token": "APP_GUARD"}),
    ("DEPENDS_ON", ("OrdersModule", N + "orders/orders.module.ts"), ("OrdersEvents", N + "orders/orders.events.ts"),
     "INFERRED", {"via": "module", "role": "controllers"}),
    ("DEPENDS_ON", ("OrdersModule", N + "orders/orders.module.ts"), ("AudioConsumer", N + "orders/audio.consumer.ts"),
     "INFERRED", {"via": "module", "role": "providers"}),
    ("DEPENDS_ON", ("OrdersModule", N + "orders/orders.module.ts"), ("EventsGateway", N + "orders/events.gateway.ts"),
     "INFERRED", {"via": "module", "role": "providers"}),
    # NestJS jobs and events
    ("SCHEDULED_AS", ("CatsService", N + "cats/cats.service.ts"), ("CatsService.purgeStale", N + "cats/cats.service.ts"),
     "EXTRACTED", {"task": "CatsService.purgeStale", "trigger": "cron", "schedule": "CronExpression.EVERY_HOUR"}),
    ("SCHEDULED_AS", ("CatsService", N + "cats/cats.service.ts"), ("CatsService.heartbeat", N + "cats/cats.service.ts"),
     "EXTRACTED", {"trigger": "interval", "schedule": "30000"}),
    ("SCHEDULED_AS", ("AudioConsumer", N + "orders/audio.consumer.ts"),
     ("AudioConsumer.transcode", N + "orders/audio.consumer.ts"), "EXTRACTED", {"queue": "audio", "job": "transcode"}),
    ("TRIGGERS", ("CatsService", N + "cats/cats.service.ts"), ("CatsService.onCatCreated", N + "cats/cats.service.ts"),
     "EXTRACTED", {"event": "cat.created"}),
    ("TRIGGERS", ("OrdersEvents", N + "orders/orders.events.ts"),
     ("OrdersEvents.handleOrderCreated", N + "orders/orders.events.ts"), "EXTRACTED",
     {"event": "order_created", "transport": "microservice"}),
    ("TRIGGERS", ("OrdersEvents", N + "orders/orders.events.ts"), ("OrdersEvents.accumulate", N + "orders/orders.events.ts"),
     "EXTRACTED", {"pattern": "{ cmd: 'sum' }", "transport": "microservice"}),
    ("TRIGGERS", ("EventsGateway", N + "orders/events.gateway.ts"), ("EventsGateway.handleEvent", N + "orders/events.gateway.ts"),
     "EXTRACTED", {"event": "events", "transport": "websocket"}),
    # TypeORM: relations, cascades in the existing direction, inheritance
    ("INHERITS", ("Cat", N + "cats/cat.entity.ts"), ("AuditedEntity", N + "common/audited.entity.ts"), "INFERRED", {}),
    ("REFERENCES", ("Cat", N + "cats/cat.entity.ts"), ("Owner", N + "owners/owner.entity.ts"), "INFERRED",
     {"decorator": "ManyToOne", "property": "owner"}),
    ("CASCADES", ("Owner", N + "owners/owner.entity.ts"), ("Cat", N + "cats/cat.entity.ts"), "INFERRED",
     {"strategy": "CASCADE", "via": "typeorm", "property": "owner"}),
    ("HAS_MANY", ("Cat", N + "cats/cat.entity.ts"), ("Toy", N + "cats/toy.entity.ts"), "INFERRED",
     {"decorator": "OneToMany", "property": "toys"}),
    ("CASCADES", ("Cat", N + "cats/cat.entity.ts"), ("Toy", N + "cats/toy.entity.ts"), "INFERRED",
     {"strategy": "remove", "via": "typeorm", "property": "toys"}),
    ("REFERENCES", ("Cat", N + "cats/cat.entity.ts"), ("Profile", N + "cats/profile.entity.ts"), "INFERRED",
     {"decorator": "OneToOne", "property": "profile"}),
    ("CASCADES", ("Profile", N + "cats/profile.entity.ts"), ("Cat", N + "cats/cat.entity.ts"), "INFERRED",
     {"strategy": "SET NULL", "via": "typeorm", "property": "profile"}),
    # TypeORM entity listeners: a Rails before_save callback's shape (HOOKS class -> its own method, LOCAL)
    ("HOOKS", ("Cat", N + "cats/cat.entity.ts"), ("Cat.normalizeName", N + "cats/cat.entity.ts"), "EXTRACTED",
     {"event": "BeforeInsert"}),
    ("HOOKS", ("Cat", N + "cats/cat.entity.ts"), ("Cat.touch", N + "cats/cat.entity.ts"), "EXTRACTED",
     {"event": "AfterLoad"}),
    ("HAS_MANY", ("Cat", N + "cats/cat.entity.ts"), ("Tag", N + "cats/tag.entity.ts"), "INFERRED",
     {"decorator": "ManyToMany", "property": "tags"}),
    ("HAS_MANY", ("Owner", N + "owners/owner.entity.ts"), ("Cat", N + "cats/cat.entity.ts"), "INFERRED",
     {"decorator": "OneToMany", "property": "cats"}),
    ("REFERENCES", ("Owner", N + "owners/owner.entity.ts"), ("Address", N + "owners/address.entity.ts"), "INFERRED",
     {"decorator": "OneToOne", "property": "address"}),
    # an entity named like a DOM global (`Event`, imported from the repo) and a default export imported under
    # another name (`import Organizer from './organizer.entity'` -> OrganizerEntity, not misc/organizer.ts)
    ("REFERENCES", ("Ticket", N + "events/ticket.entity.ts"), ("Event", N + "events/event.entity.ts"), "INFERRED",
     {"decorator": "ManyToOne", "property": "event"}),
    ("CASCADES", ("Event", N + "events/event.entity.ts"), ("Ticket", N + "events/ticket.entity.ts"), "INFERRED",
     {"strategy": "CASCADE", "via": "typeorm", "property": "event"}),
    ("REFERENCES", ("Ticket", N + "events/ticket.entity.ts"), ("OrganizerEntity", N + "events/organizer.entity.ts"),
     "INFERRED", {"decorator": "ManyToOne", "property": "organizer"}),
    # Express: src = the declaring module; a mount in another module composes the path (finalize)
    ("ROUTES_TO", ("server", "express/server.js"), ("health", "express/server.js"), "EXTRACTED",
     {"method": "GET", "path": "/health", "framework": "express"}),
    # (express/test/users.test.js also mounts this router, at the root: a test mount never shapes the path)
    ("ROUTES_TO", ("users", "express/routes/users.js"), ("listUsers", "express/routes/users.js"), "EXTRACTED",
     {"method": "GET", "path": "/api/users", "framework": "express", "paths": None}),
    ("ROUTES_TO", ("users", "express/routes/users.js"), ("createUser", "express/routes/users.js"), "INFERRED",
     {"method": "POST", "path": "/api/users", "middleware": ["validate"]}),
    ("ROUTES_TO", ("users", "express/routes/users.js"), ("getUser", "express/routes/users.js"), "EXTRACTED",
     {"method": "GET", "path": "/api/users/:id", "paths": None}),
    ("ROUTES_TO", ("users", "express/routes/users.js"), ("updateUser", "express/routes/users.js"), "EXTRACTED",
     {"method": "PATCH,PUT", "path": "/api/users/:id"}),
    # two module objects' `show` actions: two edges, each with its own path (never one folded edge)
    ("ROUTES_TO", ("mvc", "express/routes/mvc.ts"), ("show", "express/controllers/articles.ts"), "INFERRED",
     {"method": "GET", "path": "/articles/:id", "routes": None}),
    ("ROUTES_TO", ("mvc", "express/routes/mvc.ts"), ("show", "express/controllers/people.ts"), "INFERRED",
     {"method": "GET", "path": "/people/:id", "routes": None}),
    # a template after a `>` comparison in a .js file is a template: the route is /badges, never its text's /fake
    ("ROUTES_TO", ("badges", "express/routes/badges.js"), ("listBadges", "express/routes/badges.js"), "EXTRACTED",
     {"method": "GET", "path": "/badges", "framework": "express"}),
    # one handler through a renamed import and a namespace member: one row listing both routes; the local
    # function of the imported name keeps its own row
    ("ROUTES_TO", ("forms", "express/routes/forms.ts"), ("show", "express/controllers/articles.ts"), "INFERRED",
     {"method": "GET", "path": "/a/:id", "routes": ["GET /a/:id", "GET /b/:id"]}),
    ("ROUTES_TO", ("forms", "express/routes/forms.ts"), ("show", "express/routes/forms.ts"), "EXTRACTED",
     {"method": "GET", "path": "/local", "routes": None}),
    # Node.js subpath imports (`#src/*` in package.json `imports`): placed in the package's src/, not misc/
    ("ROUTES_TO", ("routes", "subpath/src/routes.ts"), ("listUsers", "subpath/src/controllers/users.ts"), "INFERRED",
     {"method": "GET", "path": "/users", "framework": "express"}),
    ("INHERITS", ("UsersRepo", "subpath/src/repos/users.repo.ts"), ("BaseRepo", "subpath/src/shared/base.repo.ts"),
     "INFERRED", {}),
    # a handler on a module-level `new LegacyController()`: the class its import names, never misc/'s decoy
    ("ROUTES_TO", ("legacy", "express/routes/legacy.ts"),
     ("LegacyController.list", "express/controllers/legacy.controller.ts"), "INFERRED",
     {"method": "GET", "path": "/legacy", "framework": "express"}),
    ("ROUTES_TO", ("orders.controller", "express/controllers/orders.controller.ts"),
     ("OrdersController.list", "express/controllers/orders.controller.ts"), "EXTRACTED",
     {"method": "GET", "path": "/orders", "framework": "express"}),
    ("ROUTES_TO", ("orders.controller", "express/controllers/orders.controller.ts"),
     ("OrdersController.create", "express/controllers/orders.controller.ts"), "EXTRACTED",
     {"method": "POST", "path": "/orders"}),
    # a class-based router over an injected controller: `this.products.<handler>` binds ProductsController's
    # handlers, `${API}${this.path}` folds; an arrow property (no engine node on 0.8.39) and a handler wrapped
    # by asyncHandler (no engine node on any version) resolve onto the recognizer's own :Method nodes
    ("ROUTES_TO", ("products.route", "express/routes/products.route.ts"),
     ("ProductsController.list", "express/controllers/products.controller.ts"), "INFERRED",
     {"method": "GET", "path": "/products", "framework": "express"}),
    ("ROUTES_TO", ("products.route", "express/routes/products.route.ts"),
     ("ProductsController.getOne", "express/controllers/products.controller.ts"), "INFERRED",
     {"method": "GET", "path": "/v2/products/:id", "middleware": ["requireAuth"]}),
    ("ROUTES_TO", ("products.route", "express/routes/products.route.ts"),
     ("ProductsController.create", "express/controllers/products.controller.ts"), "INFERRED",
     {"method": "POST", "path": "/products"}),
    # Fastify: route objects with hooks; a plugin registered with a prefix in another module (finalize)
    ("ROUTES_TO", ("server", "fastify/server.ts"), ("getStatus", "fastify/server.ts"), "EXTRACTED",
     {"method": "GET", "path": "/status", "framework": "fastify"}),
    ("ROUTES_TO", ("server", "fastify/server.ts"), ("ping", "fastify/server.ts"), "EXTRACTED",
     {"method": "GET,HEAD", "path": "/ping", "middleware": ["authenticate"]}),
    # a route object with a shorthand `handler` property
    ("ROUTES_TO", ("server", "fastify/server.ts"), ("handler", "fastify/server.ts"), "EXTRACTED",
     {"method": "GET", "path": "/ready", "framework": "fastify"}),
    ("ROUTES_TO", ("items", "fastify/routes/items.ts"), ("listItems", "fastify/routes/items.ts"), "EXTRACTED",
     {"method": "GET", "path": "/items", "framework": "fastify"}),
    ("ROUTES_TO", ("items", "fastify/routes/items.ts"), ("getItem", "fastify/routes/items.ts"), "EXTRACTED",
     {"method": "GET", "path": "/items/:id"}),
    # koa-router: the router's own prefix, a named route, del -> DELETE
    ("ROUTES_TO", ("books", "koa/books.js"), ("listBooks", "koa/books.js"), "EXTRACTED",
     {"method": "GET", "path": "/v1/books", "name": "books", "framework": "koa"}),
    ("ROUTES_TO", ("books", "koa/books.js"), ("removeBook", "koa/books.js"), "EXTRACTED",
     {"method": "DELETE", "path": "/v1/books/:id"}),
    # koa: controller objects imported through a barrel resolve onto their classes' static handlers
    ("ROUTES_TO", ("routes", "koa2/src/routes.ts"), ("GeneralController.helloWorld", "koa2/src/controller/general.ts"),
     "INFERRED", {"method": "GET", "path": "/", "framework": "koa"}),
    ("ROUTES_TO", ("routes", "koa2/src/routes.ts"), ("UserController.getUsers", "koa2/src/controller/user.ts"),
     "INFERRED", {"method": "GET", "path": "/users", "framework": "koa"}),
    # ...and behind the barrel, a module that default-exports an instance: its class's method
    ("ROUTES_TO", ("routes", "koa2/src/routes.ts"), ("StatusController.show", "koa2/src/controller/status.ts"),
     "INFERRED", {"method": "GET", "path": "/status", "framework": "koa"}),
    # zod v4: the engine node-ifies `interface ZodString` and `const ZodString`; the edge binds the interfaces
    ("INHERITS", ("ZodString", "zod/schemas.ts"), ("ZodType", "zod/schemas.ts"), "EXTRACTED", {}),
    # Hono: basePath, a sub-app mounted with app.route, app.on, an imported handler
    ("ROUTES_TO", ("index", "hono/index.ts"), ("getBook", "hono/index.ts"), "EXTRACTED",
     {"method": "GET", "path": "/api/books/:id", "framework": "hono"}),
    ("ROUTES_TO", ("index", "hono/index.ts"), ("listPosts", "hono/posts.ts"), "INFERRED",
     {"method": "GET", "path": "/api/posts"}),
    ("ROUTES_TO", ("index", "hono/index.ts"), ("updatePost", "hono/index.ts"), "EXTRACTED",
     {"method": "PUT,DELETE", "path": "/api/posts/:id"}),
    # a sub-app imported through a barrel is mounted where the barrel's import is
    ("ROUTES_TO", ("authors", "hono/routes/authors.ts"), ("listAuthors", "hono/routes/authors.ts"), "EXTRACTED",
     {"method": "GET", "path": "/api/authors", "framework": "hono"}),
    # Next.js file-system routes
    ("ROUTES_TO", ("route", "web/app/api/users/[id]/route.ts"), ("GET", "web/app/api/users/[id]/route.ts"),
     "EXTRACTED", {"method": "GET", "path": "/api/users/:id", "framework": "nextjs"}),
    ("ROUTES_TO", ("route", "web/app/api/users/[id]/route.ts"), ("DELETE", "web/app/api/users/[id]/route.ts"),
     "EXTRACTED", {"method": "DELETE", "path": "/api/users/:id"}),
    ("ROUTES_TO", ("route", "web/app/api/users/[id]/route.ts"), ("PATCH", "web/app/api/users/[id]/route.ts"),
     "EXTRACTED", {"method": "PATCH", "path": "/api/users/:id"}),
    ("ROUTES_TO", ("route", "web/app/(marketing)/api/health/route.ts"),
     ("GET", "web/app/(marketing)/api/health/route.ts"), "EXTRACTED", {"method": "GET", "path": "/api/health"}),
    ("ROUTES_TO", ("route", "web/app/docs/[...slug]/route.ts"), ("GET", "web/app/docs/[...slug]/route.ts"),
     "EXTRACTED", {"method": "GET", "path": "/docs/*slug"}),
    ("ROUTES_TO", ("[itemId]", "web/pages/api/items/[itemId].ts"), ("handler", "web/pages/api/items/[itemId].ts"),
     "EXTRACTED", {"method": "*", "path": "/api/items/:itemId", "framework": "nextjs"}),
    ("ROUTES_TO", ("index", "web/pages/api/legacy/index.js"), ("legacyHandler", "web/pages/api/legacy/index.js"),
     "EXTRACTED", {"method": "*", "path": "/api/legacy"}),
    # the app router anchored at the Next project root (next.config), not at the package directory `app`
    ("ROUTES_TO", ("route", "forge/apps/app/app/api/collab/route.ts"), ("POST", "forge/apps/app/app/api/collab/route.ts"),
     "EXTRACTED", {"method": "POST", "path": "/api/collab", "framework": "nextjs"}),
    # Angular: Routes -> components (children compose), a functional guard, constructor + inject() DI
    ("ROUTES_TO", ("app.routes", "ng/src/app/app.routes.ts"), ("HeroesComponent", "ng/src/app/heroes/heroes.component.ts"),
     "INFERRED", {"method": "VIEW", "path": "/heroes", "framework": "angular"}),
    ("ROUTES_TO", ("app.routes", "ng/src/app/app.routes.ts"),
     ("HeroDetailComponent", "ng/src/app/heroes/hero-detail.component.ts"), "INFERRED",
     {"method": "VIEW", "path": "/admin/detail/:id"}),
    # one guard on two routes: one row (edge_resolve keeps one per src, dst, type) that lists both paths
    ("HOOKS", ("app.routes", "ng/src/app/app.routes.ts"), ("authGuard", "ng/src/app/auth.guard.ts"), "INFERRED",
     {"event": "guard", "path": "/heroes", "paths": ["/heroes", "/users"]}),
    # two components exported as ListComponent by two modules: one route each, never folded together
    ("ROUTES_TO", ("app.routes", "ng/src/app/app.routes.ts"), ("ListComponent", "ng/src/app/users/list.component.ts"),
     "INFERRED", {"method": "VIEW", "path": "/users", "routes": None}),
    ("ROUTES_TO", ("app.routes", "ng/src/app/app.routes.ts"), ("ListComponent", "ng/src/app/orders/list.component.ts"),
     "INFERRED", {"method": "VIEW", "path": "/orders", "routes": None}),
    ("DEPENDS_ON", ("HeroesComponent", "ng/src/app/heroes/heroes.component.ts"),
     ("HeroService", "ng/src/app/hero.service.ts"), "INFERRED", {"via": "ctor", "param": "heroService"}),
    ("DEPENDS_ON", ("HeroesComponent", "ng/src/app/heroes/heroes.component.ts"),
     ("MessageService", "ng/src/app/message.service.ts"), "INFERRED", {"via": "inject", "field": "messages"}),
]

# `controller.remove` on a CommonJS module object resolves INSIDE that module only (a module scope): it binds
# controllers/users.js's own `remove` where the engine node-ifies `exports.remove = function remove(...)`
# (graphify 0.8.51 does, 0.8.39 does not) and is dropped otherwise — never a fan-out over the repo's other
# `remove` methods (CatsController.remove, CatsService.remove).
TOLERATED_DST_NAME = "remove"
TOLERATED_ROW = ("ROUTES_TO", ("users", "express/routes/users.js"), ("remove", "express/controllers/users.js"),
                 "INFERRED")


# =====================================================================================================
# helpers
# =====================================================================================================
def _write_app(root: Path, files: dict) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")


def _app_sha() -> str:
    return hashlib.sha256(json.dumps(APP, sort_keys=True).encode("utf-8")).hexdigest()


_CAPTURE_VERSION = "0.8.39"
_NODE_KEYS = ("id", "label", "file_type", "source_file", "source_location")
_LINK_KEYS = ("source", "target", "relation", "source_location", "source_file")


def _scrub_temp_ids(rows: list) -> list:
    """`rows` with every string field that is a graphify id built from the temp directory's path
    (`c_users_<account>_appdata_local_temp_...`: an import that resolves outside APP) starting `tmp_` instead, so a
    capture never carries the capturing account's name. Such an id names no node; capture() re-checks that the
    scrubbed fixture still maps back to the live run's nodes."""
    prefixes = set()
    for p in (tempfile.gettempdir(), str(Path(tempfile.gettempdir()).resolve())):
        slug = re.sub(r"[^a-z0-9]+", "_", p.lower()).strip("_")
        if slug and slug != "tmp":
            prefixes.add(slug + "_")
    ordered = sorted(prefixes, key=len, reverse=True)

    def fix(v):
        if isinstance(v, str):
            for p in ordered:
                if v.startswith(p):
                    return "tmp_" + v[len(p):]
        return v

    return [{k: fix(v) for k, v in r.items()} for r in rows]


def capture() -> int:
    """--capture: re-write FIXTURE from the installed engine's extraction of APP (temp dir, temp DEEPMAP_HOME,
    the production path). Refused on any engine but 0.8.39, and when the trimmed capture does not map back to
    the live run's nodes; the fixture is untouched then."""
    with tempfile.TemporaryDirectory(prefix="dmts_capture_") as td:
        base = Path(td)
        root = base / "app"
        _write_app(root, APP)
        with _env(DEEPMAP_HOME=str(base / "home"), **_FLAGS), contextlib.redirect_stderr(io.StringIO()):
            res, rep = GX.extract_with_report(root)
            ver = (rep or {}).get("graphify_version")
            if not res or ver != _CAPTURE_VERSION:
                print(f"capture refused: needs graphify {_CAPTURE_VERSION} (installed: {ver}; "
                      f"{(rep or {}).get('cause') or 'extracted'})", file=sys.stderr)
                return 1
            g = json.loads((GX.graphify_out_dir(root) / "graph.json").read_text(encoding="utf-8"))
            fx = {"_note": ("REAL captured `graphify update --no-cluster` output (graphify %s) of APP in "
                            "tests/typescript_edges_gates.py (NestJS, TypeORM, Express, Fastify, koa-router, Hono, "
                            "Next.js, Angular + look-alikes), trimmed to the fields "
                            "build/graphify_extract.map_graph reads. Zero-egress capture (--no-cluster, no LLM). "
                            "The engine node-ifies the classes / `Class.method` methods / functions / modules and "
                            "persists NONE of the framework wiring." % ver),
                  "_graphify_version": ver, "_app_sha256": _app_sha(),
                  "nodes": _scrub_temp_ids([{k: n[k] for k in _NODE_KEYS if k in n} for n in g["nodes"]]),
                  "links": _scrub_temp_ids([{k: x[k] for k in _LINK_KEYS if k in x} for x in g["links"]])}
            mapped = GX.map_graph(fx, root=root)[0]
        live = sorted((n["name"], n["label"], n["source_file"], n["start_line"]) for n in res[0])
        back = sorted((n["name"], n["label"], n["source_file"], n["start_line"]) for n in mapped)
        if live != back:
            print(f"capture refused: the trimmed capture maps to other nodes ({sorted(set(live) ^ set(back))[:5]})",
                  file=sys.stderr)
            return 1
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(fx, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"captured {len(fx['nodes'])} nodes / {len(fx['links'])} links (graphify {ver}) -> {FIXTURE}")
    return 0


def _rows(root: Path, nodes: list) -> tuple:
    """The production path: sync._apply_recognizers (registry + finalize + sync's exclusion + merge) then
    edge_resolve.edge_rows. -> ([(type, (src name, file), (dst name, file), confidence, attrs)], typed)."""
    with contextlib.redirect_stderr(io.StringIO()):
        merged, typed = S._apply_recognizers(root, nodes)
    live = [n["key"] for n in merged]
    resolver = ER.Resolver(merged)
    by = {n["key"]: (n["name"], str(n["source_file"]).replace("\\", "/")) for n in merged}
    rows = ER.edge_rows(typed, [], resolver, live, set(live))
    return [(r[0], by[r[1]], by[r[2]], r[3], r[4]) for r in rows], typed


def _ts_rows(rows: list) -> list:
    """Only the rows whose anchor is a TS/JS node (the fixture holds no other language)."""
    return [r for r in rows if r[1][1].endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"))]


def _check_rows(label: str, rows: list) -> None:
    got = {(r[0], r[1], r[2]): r for r in rows}
    for et, src, dst, conf, attrs in EXPECTED:
        r = got.get((et, src, dst))
        if r is None:
            check(f"{label}: {et} {src[0]} -> {dst[0]}", False,
                  f"missing; rows from {src[1]}: {sorted((x[0], x[2][0], x[3]) for x in rows if x[1][1] == src[1])}")
            continue
        sub = {k: r[4].get(k) for k in attrs}
        check(f"{label}: {et} {src[0]} -> {dst[0]} [{conf}] {attrs}",
              r[3] == conf and sub == attrs, f"got {r[3]} {r[4]}")
    expected_keys = {(et, src, dst) for et, src, dst, _c, _a in EXPECTED}
    extra = [r for r in rows if (r[0], r[1], r[2]) not in expected_keys]
    tolerated = [r for r in extra if r[0] == "ROUTES_TO" and r[1] == ("users", "express/routes/users.js")
                 and r[2][0].rsplit(".", 1)[-1] == TOLERATED_DST_NAME]
    unexpected = [r for r in extra if r not in tolerated]
    check(f"{label}: no row beyond the expected set (no fabricated edge)", not unexpected,
          sorted((r[0], r[1], r[2], r[3]) for r in unexpected))
    check(f"{label}: controller.remove (a member of a CommonJS module) resolves inside that module only — "
          f"its own remove, INFERRED, or nothing; never CatsController.remove / CatsService.remove",
          all((r[0], r[1], r[2], r[3]) == TOLERATED_ROW for r in tolerated),
          sorted((r[2], r[3]) for r in tolerated))
    look = [r for r in rows if r[1][1] in LOOKALIKE_FILES or r[2][1] in LOOKALIKE_FILES]
    check(f"{label}: no edge from or to a look-alike / decoy file", not look,
          sorted((r[0], r[1], r[2]) for r in look))
    seen: dict = {}
    for r in rows:
        seen[(r[0], r[1], r[2])] = seen.get((r[0], r[1], r[2]), 0) + 1
    check(f"{label}: one row per edge — never a fan-out over same-named nodes of one file (zod's interface + const)",
          all(c == 1 for c in seen.values()), sorted((k, c) for k, c in seen.items() if c > 1))


# =====================================================================================================
# E — through the real engine's extraction (captured 0.8.39 + the installed engine)
# =====================================================================================================
def gate_captured_engine(base: Path) -> None:
    print("\n-- E1: the CAPTURED graphify 0.8.39 extraction of APP, through the production seam --")
    check("the captured fixture exists", FIXTURE.is_file(), str(FIXTURE))
    if not FIXTURE.is_file():
        return
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    check("the fixture was captured from THIS APP (re-capture it after editing APP)",
          fx.get("_app_sha256") == _app_sha(), f"{fx.get('_app_sha256')} != {_app_sha()}")
    root = base / "captured"
    _write_app(root, APP)
    with _env(**_FLAGS):
        nodes, _calls, _imports = GX.map_graph(fx, root=root)
        rows, _typed = _rows(root, nodes)
    _check_rows("0.8.39", _ts_rows(rows))


def gate_live_engine(base: Path) -> None:
    print("\n-- E2: the INSTALLED engine's extraction of APP, through the production seam --")
    if not _HAS_ENGINE:
        print("     (graphify not installed — the live half is skipped; E1 still ran on the captured output)")
        return
    root = base / "live"
    _write_app(root, APP)
    with _env(DEEPMAP_HOME=str(base / "home"), **_FLAGS):
        with contextlib.redirect_stderr(io.StringIO()):
            res, report = GX.extract_with_report(root)
        if not res:
            check("the installed engine produced a graph", False, report.get("cause"))
            return
        rows, _typed = _rows(root, res[0])
    _check_rows(f"live {report.get('graphify_version')}", _ts_rows(rows))


# =====================================================================================================
# R — the recognizer alone
# =====================================================================================================
def _rec(rel: str, src: str = None):
    return T.recognize(APP[rel] if src is None else src, rel)


def _e(edges, et):
    return [x for x in edges if x.edge_type == et]


def gate_recognizer_core() -> None:
    print("\n-- R1: the per-file core: TypedEdges, conf hints, attrs (no engine) --")
    if T is None:
        check("build/typescript_edges.py exists (the recognizer)", False, "ImportError")
        return
    nodes, edges = _rec("nest/src/cats/cats.controller.ts")
    ro = {x.dst_name: x for x in _e(edges, "ROUTES_TO")}
    check("@Controller('cats') + @Get(':id') -> ROUTES_TO CatsController -> CatsController.findOne, LOCAL, "
          "path /cats/:id (no global prefix before finalize)",
          "CatsController.findOne" in ro and ro["CatsController.findOne"].src_name == "CatsController"
          and ro["CatsController.findOne"].conf_hint == "LOCAL"
          and ro["CatsController.findOne"].attrs.get("path") == "/cats/:id", str(ro))
    check("@Put + @Patch on one handler -> two route edges (finalize folds them)",
          [x.attrs.get("method") for x in _e(edges, "ROUTES_TO") if x.dst_name == "CatsController.update"]
          == ["PUT", "PATCH"])
    hooks = {(x.src_name, x.dst_name, x.conf_hint) for x in _e(edges, "HOOKS")}
    check("@UseGuards(RolesGuard) on the class + @UseInterceptors on a handler -> HOOKS (imported -> NAME)",
          hooks == {("CatsController", "RolesGuard", "NAME"),
                    ("CatsController.findOne", "LoggingInterceptor", "NAME")}, str(hooks))
    deps = {x.dst_name: x.attrs for x in _e(edges, "DEPENDS_ON")}
    check("constructor injection -> DEPENDS_ON CatsService only (ConfigService is @nestjs/config's, the "
          "number token is no class)", list(deps) == ["CatsService"], str(deps))
    check("one ensured :Class node, named + placed as the engine names it (the `class` line)",
          [(n["name"], n["start_line"], n["label"]) for n in nodes] == [("CatsController", 10, "Class")]
          and nodes[0]["body_summary"].startswith("CatsController: NestJS controller"), str(nodes))

    _n, e2 = _rec("nest/src/cats/cat.entity.ts")
    cas = {(x.src_name, x.dst_name, x.attrs.get("strategy")) for x in _e(e2, "CASCADES")}
    check("TypeORM cascades: onDelete CASCADE / SET NULL -> referenced -> declaring (a belongs-to); "
          "cascade ['remove'] -> owner -> element; ['insert', 'update'] none",
          cas == {("Owner", "Cat", "CASCADE"), ("Profile", "Cat", "SET NULL"), ("Cat", "Toy", "remove")}, str(cas))
    _n, e3 = _rec("nest/src/owners/owner.entity.ts")
    check("an onDelete on a @OneToOne WITHOUT its @JoinColumn names no principal -> no CASCADES",
          not _e(e3, "CASCADES"), str(_e(e3, "CASCADES")))
    _n, e4 = _rec("nest/src/common/audited.entity.ts")
    check("`extends BaseEntity` imported from typeorm: the reader leaves the package specifier to finalize "
          "(pref:spec:typeorm, which binds nothing unless the tree holds that module) — never a bare global name",
          [x.scope for x in _e(e4, "INHERITS")] == ["L3,0|pref:spec:typeorm"], str(e4))
    _n, e5 = _rec("nest/src/common/roles.guard.ts")
    check("`implements CanActivate` (@nestjs/common) is never an edge", not _e(e5, "IMPLEMENTS"), str(e5))

    _n, e6 = _rec("express/routes/users.js")
    ro = [(x.dst_name, x.conf_hint, x.attrs.get("method"), x.attrs.get("path"), x.scope)
          for x in _e(e6, "ROUTES_TO")]
    check("Express router: in-file handler LOCAL, wrapped final handler unwrapped at NAME, route() chain, "
          "a CommonJS module member -> its name scoped to that module; ghost / variable handlers -> nothing; "
          "router-relative paths",
          ro == [("listUsers", "LOCAL", "GET", "/", ""), ("createUser", "NAME", "POST", "/", ""),
                 ("getUser", "LOCAL", "GET", "/:id", ""), ("updateUser", "LOCAL", "PUT", "/:id", ""),
                 ("updateUser", "LOCAL", "PATCH", "/:id", ""),
                 ("remove", "NAME", "DELETE", "/:id", "module:express/controllers/users")], str(ro))
    check("route src is the declaring module (the file stem)", {x.src_name for x in _e(e6, "ROUTES_TO")} == {"users"})

    _n, e7 = _rec("express/server.js")
    check("a same-file mount is composed in the core only for a router THIS file declares (usersRouter is "
          "imported: the path stays router-relative until finalize)",
          [(x.dst_name, x.attrs.get("path")) for x in _e(e7, "ROUTES_TO")] == [("health", "/health")], str(e7))

    rf = T._RouterFacts(T.analyze(APP["express/routes/index.js"], "express/routes/index.js"))
    check("the `[{ path, route }].forEach((e) => router.use(e.path, e.route))` idiom over a literal array -> "
          "one mount per element", [(m[0], m[1], m[2]) for m in rf.mounts]
          == [(("var", "router"), ("import", "./users", "="), "/users")], rf.mounts)
    dstr = APP["express/routes/index.js"].replace("(entry) => {\n  router.use(entry.path, entry.route);",
                                                  "({ path, route }) => {\n  router.use(path, route);")
    rf2 = T._RouterFacts(T.analyze(dstr, "express/routes/index.js"))
    check("...and its destructured form `({ path, route }) => router.use(path, route)`",
          [(m[1], m[2]) for m in rf2.mounts] == [(("import", "./users", "="), "/users")], rf2.mounts)
    _n, em = _rec("nest/src/cats/cats.module.ts")
    hk = [(x.src_name, x.dst_name, x.conf_hint, x.attrs) for x in _e(em, "HOOKS")]
    check("NestJS module middleware: consumer.apply(LoggerMiddleware)...forRoutes(...) in configure() -> HOOKS "
          "module -> middleware, the covered routes in attrs; `implements NestModule` is no edge",
          hk == [("CatsModule", "LoggerMiddleware", "NAME",
                  {"event": "middleware", "routes": ["CatsController", "cats/* POST"]})]
          and not _e(em, "IMPLEMENTS"), str(em))

    _n, e8 = _rec("web/app/_lib/internal/route.ts")
    check("Next.js: a route.ts under a _private folder is not routable -> nothing", e8 == [], str(e8))
    _n, e9 = _rec("misc/lookalikes.ts")
    check("look-alikes: strings, templates, comments, Map.get, axios.get, an axios instance -> nothing",
          e9 == [], str(e9))
    _n, e10 = _rec("misc/widget.tsx")
    check("JSX text with quotes / slashes neither crashes nor emits", e10 == [], str(e10))


def gate_skips_and_shapes() -> None:
    print("\n-- R2: skipped files, external imports, engine-shaped names --")
    if T is None:
        check("build/typescript_edges.py exists (the recognizer)", False, "ImportError")
        return
    ctrl = APP["nest/src/cats/cats.controller.ts"]
    for rel in ("types/cats.d.ts", "public/app.min.js", "vendor.bundle.js", "lib/x-min.js"):
        check(f"{rel} is skipped (declaration / minified / bundle)", T.recognize(ctrl, rel) == ([], []))
    bundle = "var a=1;" * 400 + "\n" + " ".join(ctrl.split("\n"))
    check("a file that looks minified (a 2000+ char line, lines averaging 200+ chars) is skipped",
          T.recognize(bundle, "src/x.ts") == ([], []))
    long_string = ctrl.replace("@Controller('cats')", "const ICON = '" + "x" * 3000 + "';\n\n@Controller('cats')")
    check("...but a hand-written controller holding one long string keeps its wiring",
          len(T.recognize(long_string, "src/x.ts")[1]) == len(T.recognize(ctrl, "src/x.ts")[1]) > 0)
    dup = ctrl.replace("import { RolesGuard } from '../common/roles.guard';",
                       "import { RolesGuard } from '@nestjs/roles';")
    _n, e = T.recognize(dup, "src/c.ts")
    check("a guard imported from a third-party scope (@nestjs/*) is never an edge, even with a repo class of "
          "that name elsewhere", not any(x.dst_name == "RolesGuard" for x in e), str(e))
    ws = ctrl.replace("import { RolesGuard } from '../common/roles.guard';",
                      "import { RolesGuard } from '@acme/auth';")
    _n, e = T.recognize(ws, "src/c.ts")
    check("...but one imported from an unknown @scope (a workspace package) is kept (resolution decides)",
          any(x.dst_name == "RolesGuard" for x in e), str(e))
    generic = "export class Repo<T extends Base = Base> extends Store<T> implements IRepo<T> {}\n"
    _n, e = T.recognize(generic, "src/repo.ts")
    check("`class Repo<T extends Base> extends Store<T>`: the base is Store (the constraint is no base)",
          [(x.edge_type, x.dst_name) for x in e] == [("INHERITS", "Store"), ("IMPLEMENTS", "IRepo")], str(e))
    expr = "export const Mixin = class Inner extends Base {};\nfunction f() { class Local extends Base {} }\n"
    n, e = T.recognize(expr, "src/m.ts")
    check("a class expression / a class inside a function gets no ensured node (the engine node-ifies neither)",
          n == [], str(n))
    iface = "export interface Auditable extends HasId, Timestamped { audit(): void; }\n"
    n, e = T.recognize(iface, "src/a.ts")
    check("`interface X extends A, B` -> INHERITS A, B + an ensured node (the engine node-ifies interfaces)",
          [(x.edge_type, x.dst_name) for x in e] == [("INHERITS", "HasId"), ("INHERITS", "Timestamped")]
          and [x["name"] for x in n] == ["Auditable"], f"{e} {n}")
    rx = ("@Controller()\nexport class Sums {\n  @MessagePattern(/^math\\.sum\\..+$/i)\n  sum() {}\n"
          "  @EventPattern(/a\\/b/)\n  ev() {}\n}\n")
    _n, e = T.recognize(rx, "src/sums.controller.ts")
    check("a regex-literal message pattern keeps its source text in attrs (the body is blanked, never the slashes)",
          [x.attrs.get("pattern") for x in e] == ["/^math\\.sum\\..+$/i", "/a\\/b/"], str(e))
    tsoa = ("import { Controller, Get, Post, Route, Path } from 'tsoa';\n\n@Route('users')\n"
            "export class UsersController extends Controller {\n  @Get('{userId}')\n  public getUser(@Path() userId: number) {}\n"
            "  @Post()\n  public createUser() {}\n}\n")
    _n, e = T.recognize(tsoa, "src/users/usersController.ts")
    check("tsoa: `@Route('users')` is the controller prefix, the framework comes from the decorators' import; "
          "its `Controller` base class (from tsoa) is left to finalize as the package's (pref:spec:tsoa)",
          [(x.edge_type, x.dst_name, x.attrs.get("path"), x.attrs.get("framework"), x.scope) for x in e]
          == [("INHERITS", "Controller", None, None, "L4,0|pref:spec:tsoa"),
              ("ROUTES_TO", "UsersController.getUser", "/users/{userId}", "tsoa", ""),
              ("ROUTES_TO", "UsersController.createUser", "/users", "tsoa", "")], str(e))
    rc = ("import { JsonController, Get } from 'routing-controllers';\n\n@JsonController('/orders')\n"
          "export class OrderController {\n  @Get('/:id')\n  one() {}\n}\n")
    _n, e = T.recognize(rc, "src/order.controller.ts")
    check("routing-controllers: `@JsonController('/orders')` composes the path; framework routing-controllers",
          [(x.dst_name, x.attrs.get("path"), x.attrs.get("framework")) for x in e]
          == [("OrderController.one", "/orders/:id", "routing-controllers")], str(e))
    js_nest = ("import { Controller, Dependencies, Get } from '@nestjs/common';\n"
               "import { CatsService } from './cats.service';\n\n@Controller('cats')\n@Dependencies(CatsService)\n"
               "export class CatsController {\n  constructor(catsService) {\n    this.catsService = catsService;\n"
               "  }\n\n  @Get()\n  findAll() {\n    return this.catsService.findAll();\n  }\n}\n")
    _n, e = T.recognize(js_nest, "src/cats/cats.controller.js")
    check("NestJS in plain JavaScript (Babel decorators): `@Dependencies(CatsService)` -> DEPENDS_ON (untyped "
          "constructor), routes as in TypeScript",
          [(x.edge_type, x.dst_name, x.scope) for x in e]
          == [("DEPENDS_ON", "CatsService", "pref:module:src/cats/cats.service"),
              ("ROUTES_TO", "CatsController.findAll", "")], str(e))
    di = ("import { injectable, inject } from 'tsyringe';\nimport { Service } from 'typedi';\n"
          "import { AuthService } from './auth.service';\nimport { UserRepo } from './user.repo';\n\n"
          "@injectable()\nexport class AuthController {\n"
          "  constructor(@inject(AuthService) private readonly auth: AuthService) {}\n}\n\n"
          "@Service()\nexport class UserService {\n  constructor(private readonly repo: UserRepo) {}\n}\n\n"
          "export class Plain {\n  constructor(private readonly repo: UserRepo) {}\n}\n")
    _n, e = T.recognize(di, "src/di.ts")
    check("tsyringe / InversifyJS `@injectable()` and TypeDI `@Service()` classes: constructor parameters -> "
          "DEPENDS_ON; a class no DI container builds gets none",
          [(x.edge_type, x.src_name, x.dst_name) for x in e]
          == [("DEPENDS_ON", "AuthController", "AuthService"), ("DEPENDS_ON", "UserService", "UserRepo")], str(e))
    mikro = ("import { Entity, ManyToOne, OneToMany, Collection, Cascade, PrimaryKey } from '@mikro-orm/core';\n"
             "import { Author } from './author.entity';\nimport { Chapter } from './chapter.entity';\n\n"
             "@Entity()\nexport class Book {\n  @PrimaryKey()\n  id!: number;\n\n"
             "  @ManyToOne(() => Author, { deleteRule: 'cascade' })\n  author!: Author;\n\n"
             "  @OneToMany(() => Chapter, (c) => c.book, { cascade: [Cascade.REMOVE] })\n"
             "  chapters = new Collection<Chapter>(this);\n}\n")
    n, e = T.recognize(mikro, "src/book.entity.ts")
    check("MikroORM: relations -> REFERENCES / HAS_MANY, `deleteRule: 'cascade'` -> CASCADES Author -> Book, "
          "`cascade: [Cascade.REMOVE]` -> CASCADES Book -> Chapter, named as MikroORM's (not TypeORM's)",
          [(x.edge_type, x.src_name, x.dst_name, x.attrs.get("strategy"), x.attrs.get("via")) for x in e]
          == [("REFERENCES", "Book", "Author", None, None), ("CASCADES", "Author", "Book", "CASCADE", "mikro-orm"),
              ("HAS_MANY", "Book", "Chapter", None, None), ("CASCADES", "Book", "Chapter", "remove", "mikro-orm")]
          and n[0]["body_summary"].startswith("Book: MikroORM entity"), f"{e} {n}")


def _routes(edges) -> list:
    return [(x.dst_name, x.conf_hint, x.attrs.get("method"), x.attrs.get("path"), x.attrs.get("framework"), x.scope)
            for x in _e(edges, "ROUTES_TO")]


TYPED_ROUTES = """import type { Router } from 'express';
import express from 'express';
import { listUsers, getUser } from '../controllers/users';

export function userRoutes(r: Router): void {
  r.get('/users', listUsers);
}

export function adminRoutes(api: express.Router) {
  api.post('/admin', getUser);
}

interface Pager { get(path: string, h: unknown): void }
export function paged(p: Pager) {
  p.get('/pages', listUsers);
}

export function mountAll(list: Router[]) {
  list.get('/all', listUsers);
}
"""

CLASS_ROUTES = """import { Router } from 'express';
import { container } from 'tsyringe';
import { UsersController } from '../controllers/users.controller';
import { AuthController } from '../controllers/auth.controller';
import { AuditController } from '../controllers/audit.controller';
import { AuthMiddleware } from '../middlewares/auth.middleware';

const API = '/v2';

export class UsersRoute {
  public path = '/users';
  public router: Router = Router();
  private readonly users: UsersController;
  public audit = new AuditController();

  constructor(private auth: AuthController) {
    this.users = container.resolve(UsersController);
    this.initializeRoutes();
  }

  private initializeRoutes() {
    this.router.get(`${this.path}`, this.users.getUsers);
    this.router.get(`${this.path}/:id`, AuthMiddleware, this.users.getUserById);
    this.router.post(`${API}/login`, this.auth.logIn);
    this.router.get(`${this.path}/audit`, this.audit.list.bind(this.audit));
    this.router.delete(`${this.path}/${this.idParam()}`, this.users.deleteUser);
    this.router.patch(this.path, this.users.updateUser);
  }
}

export class LegacyRoute {
  public base = process.env.BASE;
  public router = Router();

  constructor(private users: UsersController, private settings: Map<string, string>) {}

  init() {
    this.router.get(`${this.base}/x`, this.users.getUsers);
    this.router.get('/y', this.settings.get);
  }
}
"""


def gate_class_routers() -> None:
    print("\n-- R3: typed receivers, class-based routers, folded template paths --")
    if T is None:
        check("build/typescript_edges.py exists (the recognizer)", False, "ImportError")
        return
    users = "pref:module:src/controllers/users"
    _n, e = T.recognize(TYPED_ROUTES, "src/routes/typed.ts")
    check("a parameter typed with a framework router type is a router whatever its name (`r: Router`, "
          "`api: express.Router`); a local `Pager` type and a `Router[]` array are not",
          _routes(e) == [("listUsers", "NAME", "GET", "/users", "express", users),
                         ("getUser", "NAME", "POST", "/admin", "express", users)], str(_routes(e)))
    plugin = ("import { FastifyInstance } from 'fastify';\nimport { health } from './health.handlers';\n\n"
              "export default async function healthRoutes(srv: FastifyInstance) {\n  srv.get('/health', health);\n}\n")
    _n, e = T.recognize(plugin, "src/plugins/health.ts")
    rf = T._RouterFacts(T.analyze(plugin, "src/plugins/health.ts"))
    check("a Fastify plugin whose first parameter is typed `FastifyInstance` (named `srv`) is a plugin "
          "function: its routes are read and a register() prefix can compose onto them",
          _routes(e) == [("health", "NAME", "GET", "/health", "fastify", "pref:module:src/plugins/health.handlers")]
          and rf.fns.get("healthRoutes", ("",))[0] == "srv", f"{_routes(e)} {rf.fns}")
    hono = ("import { Hono } from 'hono';\nimport { listPosts } from './posts';\n\n"
            "export const mountPosts = (api: Hono) => {\n  api.get('/posts', listPosts);\n};\n")
    koa = ("import Router from '@koa/router';\nimport { listBooks } from './books.handlers';\n\n"
           "export function bookRoutes(r: Router) {\n  r.get('/books', listBooks);\n}\n")
    typed_const = ("import { Express } from 'express';\nimport { createApp } from './app';\n\nfunction ping() {}\n\n"
                   "const app: Express = createApp();\napp.get('/ping', ping);\n")
    got = [_routes(T.recognize(src, rel)[1]) for src, rel in ((hono, "src/routes/posts.route.ts"),
                                                               (koa, "src/routes/books.ts"),
                                                               (typed_const, "src/server.ts"))]
    check("...the same for Hono (`api: Hono`), koa-router (`r: Router` default-imported from @koa/router) and a "
          "const typed `Express` built by a factory",
          got == [[("listPosts", "NAME", "GET", "/posts", "hono", "pref:module:src/routes/posts")],
                  [("listBooks", "NAME", "GET", "/books", "koa", "pref:module:src/routes/books.handlers")],
                  [("ping", "LOCAL", "GET", "/ping", "express", "")]], str(got))
    members = ("import { Router } from 'express';\nimport { OrdersController } from './orders.controller';\n\n"
               "export class OrdersRoutes {\n  public api!: Router;\n\n"
               "  constructor(private readonly router: Router, private readonly orders: OrdersController) {}\n\n"
               "  register() {\n    this.router.get('/orders', this.orders.list);\n"
               "    this.api.post('/orders', this.orders.create);\n  }\n}\n")
    oc = "pref:module:src/orders/orders.controller"
    check("a class field (`api!: Router`) or constructor parameter property (`private readonly router: Router`) "
          "typed as a router makes `this.<name>` a router",
          _routes(T.recognize(members, "src/orders/orders.routes.ts")[1])
          == [("OrdersController.list", "NAME", "GET", "/orders", "express", oc),
              ("OrdersController.create", "NAME", "POST", "/orders", "express", oc)],
          str(_routes(T.recognize(members, "src/orders/orders.routes.ts")[1])))
    ng = ("import { Router } from '@angular/router';\nimport type { Express } from 'express';\n\n"
          "export function boot(app: Express) {\n  return app;\n}\n\n"
          "export function go(nav: Router) {\n  nav.get('/x', go);\n}\n")
    check("a `Router` imported from a non-HTTP module (@angular/router) never makes a receiver, even beside an "
          "express-typed one",
          _e(T.recognize(ng, "src/app/go.ts")[1], "ROUTES_TO") == [])

    _n, e = T.recognize(CLASS_ROUTES, "src/routes/users.route.ts")
    uc, ac = "pref:module:src/controllers/users.controller", "pref:module:src/controllers/auth.controller"
    check("class-based router: `this.<field>.<method>` handlers bind the field's class (a typed field, a "
          "`new X()` field, a constructor parameter property); `${this.path}` / `${API}` fold to their literals",
          _routes(e) == [("UsersController.getUsers", "NAME", "GET", "/users", "express", uc),
                         ("UsersController.getUserById", "NAME", "GET", "/users/:id", "express", uc),
                         ("AuthController.logIn", "NAME", "POST", "/v2/login", "express", ac),
                         ("AuditController.list", "NAME", "GET", "/users/audit", "express",
                          "pref:module:src/controllers/audit.controller"),
                         ("UsersController.deleteUser", "NAME", "DELETE", None, "express", uc),
                         ("UsersController.updateUser", "NAME", "PATCH", "/users", "express", uc),
                         ("UsersController.getUsers", "NAME", "GET", None, "express", uc)], str(_routes(e)))
    ro = _e(e, "ROUTES_TO")
    check("...a template that does not fold (a call, a non-literal field) keeps its source text as "
          "path_unresolved; middleware before the handler is named; a field typed `Map` is no handler class",
          len(ro) == 7 and ro[4].attrs.get("path_unresolved") == "`${this.path}/${this.idParam()}`"
          and ro[6].attrs.get("path_unresolved") == "`${this.base}/x`"
          and ro[1].attrs.get("middleware") == ["AuthMiddleware"]
          and {x.src_name for x in ro} == {"users.route"}, str([x.attrs for x in ro]))
    ctl = ("import { Request, Response, RequestHandler } from 'express';\n"
           "import { asyncHandler } from '../utils/asyncHandler';\n\n"
           "export class UsersController {\n  constructor(private readonly service: UsersService) {}\n\n"
           "  getUsers = asyncHandler(async (req: Request, res: Response) => {\n"
           "    res.json(await this.service.all());\n  });\n\n"
           "  public getUserById: RequestHandler = async (req, res) => {\n    res.json({});\n  };\n\n"
           "  static parse = function (raw: string) {\n    return raw;\n  };\n\n"
           "  private readonly logger = new Logger('users');\n  count = 0;\n"
           "  total = computed(() => this.count);\n  #secret = () => 1;\n\n  list() {}\n}\n")
    n, _e2 = T.recognize(ctl, "src/controllers/users.controller.ts")
    check("function-valued class properties (an arrow, a function expression, a handler wrapped by "
          "asyncHandler) get :Method nodes `Class.prop` at their line, as graphify 0.8.51 names its own; a "
          "value, a `new`, a non-wrapper call, a #private name and a real method (the engine's) get none",
          [(x["label"], x["name"], x["start_line"]) for x in n]
          == [("Class", "UsersController", 4), ("Method", "UsersController.getUsers", 7),
              ("Method", "UsersController.getUserById", 11), ("Method", "UsersController.parse", 15)]
          and "asyncHandler" in n[1]["body_summary"], str(n))
    mount = ("import express from 'express';\nimport { usersRouter } from './users.router';\n\n"
             "const API_PREFIX = '/api/v1';\nconst app = express();\napp.use(API_PREFIX, usersRouter);\n"
             "app.use(process.env.PREFIX, usersRouter);\n")
    rf = T._RouterFacts(T.analyze(mount, "src/app.ts"))
    check("a mount prefix held by a const of the file is that literal (`app.use(API_PREFIX, r)`); one read from "
          "the environment stays unresolved", [m[2] for m in rf.mounts] == ["/api/v1", None], str(rf.mounts))
    loop = ("import { Router } from 'express';\n\nconst A = `${B}/a`;\nconst B = `${A}/b`;\nconst router = Router();\n"
            "function h() {}\nrouter.get(`${A}/x`, h);\n")
    _n, e = T.recognize(loop, "src/loop.ts")
    check("templates that reference each other in a cycle stop folding (bounded) and stay unresolved",
          [(x.dst_name, x.attrs.get("path"), x.attrs.get("path_unresolved")) for x in e]
          == [("h", None, "`${A}/x`")], str(e))
    scoped = ("import { Router } from 'express';\n\nconst P = '/one';\nlet L = '/l';\nconst router = Router();\n"
              "function h() {}\nfunction a() {\n  const Q = '/a';\n  router.get(`${Q}/x`, h);\n}\n"
              "function b() {\n  const Q = '/b';\n  router.get(`${Q}/y`, h);\n}\n"
              "router.get(`${L}/z`, h);\nrouter.get(`${P}/w`, h);\n")
    _n, e = T.recognize(scoped, "src/scoped.ts")
    check("only a `const` declared once in the file folds: a name declared in two functions, or a `let`, is "
          "left unresolved (its value at the call is not knowable from here)",
          [x.attrs.get("path") for x in e] == [None, None, None, "/one/w"], str([x.attrs for x in e]))


def _tree_edges(base: Path, name: str, files: dict) -> list:
    """The TypeScript entry's edges over a small tree: the per-file reader plus its finalize post-pass,
    as language_recognizers.run (the registry seam sync calls) produces them."""
    root = base / "r4" / name
    _write_app(root, files)
    with _env(**dict(_FLAGS, DEEPMAP_RECOGNIZERS="typescript")), contextlib.redirect_stderr(io.StringIO()):
        return LR.run(root)[1]


def _rt(edges) -> list:
    return [(x.src_file, x.dst_name, x.attrs.get("method"), x.attrs.get("path"), x.attrs.get("paths"),
             x.attrs.get("routes"), x.scope) for x in _e(edges, "ROUTES_TO")]


def _nest_ctrl(name: str, prefix: str) -> str:
    return (f"import {{ Controller, Get }} from '@nestjs/common';\n\n@Controller('{prefix}')\n"
            f"export class {name} {{\n  @Get()\n  all() {{\n    return [];\n  }}\n}}\n")


def _nest_main(prefix_line: str = "") -> str:
    return ("import { NestFactory } from '@nestjs/core';\nimport { AppModule } from './app.module';\n\n"
            "async function bootstrap() {\n  const app = await NestFactory.create(AppModule);\n"
            f"{prefix_line}  await app.listen(3000);\n}}\nbootstrap();\n")


def gate_routes_mounts_paths(base: Path) -> None:
    print("\n-- R4: routes, mounts and paths: folding, global prefixes, mount idioms, path folding --")
    if T is None:
        check("build/typescript_edges.py exists (the recognizer)", False, "ImportError")
        return
    # ---- folding: only the SAME handler (name + module scope) folds into one edge
    mvc = {"routes.js": ("const express = require('express');\nconst asyncHandler = require('express-async-handler');\n"
                         "const users = require('./controllers/users');\n"
                         "const articles = require('./controllers/articles');\nconst router = express.Router();\n"
                         "function local(req, res) {}\n"
                         "router.get('/articles/:id', articles.show);\nrouter.get('/users/:id', users.show);\n"
                         "router.post('/users/:id/show', users.show);\nrouter.get('/l', local);\n"
                         "router.post('/l2', asyncHandler(local));\nmodule.exports = router;\n"),
           "controllers/users.js": "exports.show = function show(req, res) {};\n",
           "controllers/articles.js": "exports.show = function show(req, res) {};\n"}
    got = _rt(_tree_edges(base, "fold", mvc))
    check("two module objects' `show` actions are two edges, each with its own path, placed at its own module's "
          "`exports.show` definition; one handler routed twice still folds into one edge listing both routes; a "
          "handler folds only with the same hint (LOCAL `local` and the NAME `asyncHandler(local)`), so a folded edge "
          "resolves as each route would",
          got == [("routes.js", "show", "GET", "/articles/:id", None, None, "L0,1|exact:module:controllers/articles"),
                  ("routes.js", "show", "GET", "/users/:id", None, ["GET /users/:id", "POST /users/:id/show"],
                   "L0,1|exact:module:controllers/users"),
                  ("routes.js", "local", "GET", "/l", None, None, ""),
                  ("routes.js", "local", "POST", "/l2", None, None, "")], got)
    alias = {"routes.ts": ("import { Router } from 'express';\nimport { list as listUsers } from './users';\n"
                           "import { list as listOrders } from './orders';\nconst router = Router();\n"
                           "router.get('/users', listUsers);\nrouter.get('/orders', listOrders);\nexport default router;\n"),
             "users.ts": "export function list() {}\n", "orders.ts": "export function list() {}\n"}
    got = _rt(_tree_edges(base, "fold_alias", alias))
    check("two handlers imported under aliases (`list as listUsers`, `list as listOrders`) stay two edges, each "
          "placed in its module",
          got == [("routes.ts", "list", "GET", "/users", None, None, "L0,1|exact:module:users"),
                  ("routes.ts", "list", "GET", "/orders", None, None, "L0,1|exact:module:orders")], got)
    guards = {"src/app/app.routes.ts": (
        "import { Routes } from '@angular/router';\nimport { requireAuth } from './auth.guard';\n"
        "import { SettingsComponent } from './settings.component';\nimport { EditorComponent } from './editor.component';\n"
        "export const routes: Routes = [\n"
        "  { path: 'settings', component: SettingsComponent, canActivate: [requireAuth] },\n"
        "  { path: 'editor', component: EditorComponent, canActivate: [requireAuth] },\n"
        "  { path: 'editor/:slug', component: EditorComponent, canActivate: [requireAuth] },\n];\n"),
        "src/app/auth.guard.ts": "export const requireAuth = () => true;\n"}
    hk = [(x.dst_name, x.attrs) for x in _e(_tree_edges(base, "ng_guards", guards), "HOOKS")]
    check("one Angular guard on three routes -> ONE HOOKS edge: `paths` all three, sorted, and `path` the first of "
          "them (the shared route-attr contract)",
          hk == [("requireAuth", {"event": "guard", "path": "/editor",
                                  "paths": ["/editor", "/editor/:slug", "/settings"]})], hk)

    # ---- the NestJS global prefix belongs to its own app (the bootstrap file's directory)
    two_apps = {"apps/a/src/main.ts": _nest_main("  app.setGlobalPrefix('api');\n"),
                "apps/a/src/cats.controller.ts": _nest_ctrl("CatsController", "cats"),
                "apps/b/src/main.ts": _nest_main(),
                "apps/b/src/dogs.controller.ts": _nest_ctrl("DogsController", "dogs"),
                "libs/shared/src/health.controller.ts": _nest_ctrl("HealthController", "health")}
    got = {x.src_file: (x.attrs.get("path"), x.attrs.get("global_prefix"))
           for x in _tree_edges(base, "gp_apps", two_apps)}
    check("monorepo: app a's setGlobalPrefix('api') prefixes app a's routes only; app b (its own bootstrap, no "
          "prefix) and a library outside every app get none",
          got == {"apps/a/src/cats.controller.ts": ("/api/cats", "api"),
                  "apps/b/src/dogs.controller.ts": ("/dogs", None),
                  "libs/shared/src/health.controller.ts": ("/health", None)}, got)
    nx = {"src/main.ts": _nest_main("  const globalPrefix = 'api';\n  app.setGlobalPrefix(globalPrefix);\n"),
          "src/cats/cats.controller.ts": _nest_ctrl("CatsController", "cats")}
    got = [(x.attrs.get("path"), x.attrs.get("global_prefix")) for x in _tree_edges(base, "gp_nx", nx)]
    check("the Nx template `const globalPrefix = 'api'; app.setGlobalPrefix(globalPrefix)` folds to 'api'",
          got == [("/api/cats", "api")], got)
    spec = ("describe('e2e', () => {\n  it('runs', async () => {\n"
            "    const app = moduleRef.createNestApplication();\n"
            "    app.setGlobalPrefix('{p}');\n    await app.init();\n  });\n});\n")
    tst = {"src/main.ts": _nest_main(), "src/cats.controller.ts": _nest_ctrl("CatsController", "cats"),
           "test/app.e2e-spec.ts": spec.replace("{p}", "v9"),
           "src/cats.controller.spec.ts": spec.replace("{p}", "v8")}
    got = [(x.attrs.get("path"), x.attrs.get("global_prefix")) for x in _tree_edges(base, "gp_test", tst)]
    check("a test file's setGlobalPrefix (an e2e spec in test/, or a spec beside the controllers, inside the "
          "app's directory) never prefixes production routes", got == [("/cats", None)], got)
    nested = {"src/main.ts": _nest_main("  app.setGlobalPrefix('api');\n"),
              "src/cats.controller.ts": _nest_ctrl("CatsController", "cats"),
              "src/setup/prefix.ts": "export function setup(app) {\n  app.setGlobalPrefix('api');\n}\n",
              "src/admin/main.ts": _nest_main(),
              "src/admin/admin.controller.ts": _nest_ctrl("AdminController", "admin")}
    got = {x.src_file: x.attrs.get("path") for x in _tree_edges(base, "gp_nested", nested)}
    check("the nearest bootstrap directory decides: a nested app with no prefix gets none; a prefix set by a "
          "helper below main.ts belongs to main.ts's app",
          got == {"src/cats.controller.ts": "/api/cats", "src/admin/admin.controller.ts": "/admin"}, got)
    subdir = {"src/bootstrap/main.ts": _nest_main("  app.setGlobalPrefix('api');\n"),
              "src/bootstrap/crud.controller.ts": ("import { Get } from '@nestjs/common';\n\n"
                                                   "export abstract class CrudController {\n  @Get(':id')\n  one() {}\n}\n"),
              "src/modules/cats/cats.controller.ts": _nest_ctrl("CatsController", "cats"),
              "apps/b/src/main.ts": _nest_main(),
              "apps/b/src/dogs.controller.ts": _nest_ctrl("DogsController", "dogs"),
              "apps/b/src/worker/main.ts": _nest_main("  app.setGlobalPrefix('jobs');\n")}
    got = {x.src_file: (x.attrs.get("path"), x.attrs.get("global_prefix")) for x in _tree_edges(base, "gp_subdir", subdir)}
    check("a bootstrap file in a directory of its own (src/bootstrap/main.ts, beside only a CRUD base's routes, which "
          "its subclasses serve) prefixes its app's routes (src/modules/**: the nearest directory above it holding "
          "routes); a second bootstrap below an app's root (apps/b/src/worker/main.ts) never reaches that app's routes",
          got == {"src/modules/cats/cats.controller.ts": ("/api/cats", "api"),
                  "src/bootstrap/crud.controller.ts": ("/:id", None),
                  "apps/b/src/dogs.controller.ts": ("/dogs", None)}, got)
    roots = {"seed": {"src/bootstrap/main.ts": _nest_main("  app.setGlobalPrefix('api');\n"),
                      "src/database/seeds/seed.ts": (
                          "import { NestFactory } from '@nestjs/core';\nimport { SeedModule } from './seed.module';\n"
                          "async function run() {\n  const ctx = await NestFactory.createApplicationContext(SeedModule);\n"
                          "  await ctx.close();\n}\nrun();\n"),
                      "src/modules/cats/cats.controller.ts": _nest_ctrl("CatsController", "cats")},
             "alias": {"apps/docs/src/bootstrap/main.ts": _nest_main("  app.setGlobalPrefix('docs');\n"),
                       "apps/api/src/main.ts": (
                           "import { NestFactory as NF } from '@nestjs/core';\nimport { ApiModule } from './api.module';\n"
                           "async function bootstrap() {\n  const app = await NF.create(ApiModule);\n"
                           "  await app.listen(3000);\n}\nbootstrap();\n"),
                       "apps/api/src/dogs/dogs.controller.ts": _nest_ctrl("DogsController", "dogs")},
             "package": {"apps/admin/package.json": '{ "name": "admin" }\n',
                         "apps/admin/src/bootstrap/main.ts": _nest_main("  app.setGlobalPrefix('admin');\n"),
                         "apps/shop/src/main.ts": "import { createApp } from '@acme/kit';\ncreateApp();\n",
                         "apps/shop/src/orders/orders.controller.ts": _nest_ctrl("OrdersController", "orders")}}
    got = {name: {x.src_file: (x.attrs.get("path"), x.attrs.get("global_prefix"))
                  for x in _tree_edges(base, "gp_" + name, files)} for name, files in roots.items()}
    check("only an HTTP bootstrap (`NestFactory.create`, also through an alias `NestFactory as NF`) is an app root: a "
          "seed script's createApplicationContext does not stop src/bootstrap/main.ts's walk up to src/modules; the "
          "walk stops at another app's aliased bootstrap (apps/api) and never leaves the bootstrap's own package "
          "(apps/admin), so neither 'docs' nor 'admin' prefixes another app's routes",
          got == {"seed": {"src/modules/cats/cats.controller.ts": ("/api/cats", "api")},
                  "alias": {"apps/api/src/dogs/dogs.controller.ts": ("/dogs", None)},
                  "package": {"apps/shop/src/orders/orders.controller.ts": ("/orders", None)}}, got)
    shared = {"apps/docs/src/bootstrap/main.ts": _nest_main("  app.setGlobalPrefix('docs');\n"),
              "apps/api/src/main.ts": "import { start } from '@acme/core';\nimport { ApiModule } from './api.module';\nstart(ApiModule);\n",
              "apps/api/src/cats/cats.controller.ts": _nest_ctrl("CatsController", "cats"),
              "libs/core/src/index.ts": ("import { NestFactory } from '@nestjs/core';\nexport async function start(m: any) {\n"
                                         "  const app = await NestFactory.create(m);\n  await app.listen(3000);\n}\n")}
    ws = {"nx": dict(shared, **{"package.json": '{ "name": "nx-mono" }\n', "nx.json": "{}\n",
                                "apps/docs/project.json": '{ "name": "docs" }\n', "apps/api/project.json": '{ "name": "api" }\n'}),
          "workspace": dict(shared, **{"package.json": '{ "name": "ng-mono" }\n',
                                       "angular.json": ('{ "projects": { "docs": { "root": "apps/docs" }, '
                                                        '"api": { "root": "apps/api" } } }\n')})}
    got = {name: {x.src_file: (x.attrs.get("path"), x.attrs.get("global_prefix"))
                  for x in _tree_edges(base, "gp_ws_" + name, files)} for name, files in ws.items()}
    check("an Nx project (its project.json) or a workspace project root (angular.json / workspace.json `projects`) "
          "bounds the walk up too: with one root package.json and the api app bootstrapping through a shared library, "
          "the docs app's 'docs' prefix never reaches apps/api",
          got == {"nx": {"apps/api/src/cats/cats.controller.ts": ("/cats", None)},
                  "workspace": {"apps/api/src/cats/cats.controller.ts": ("/cats", None)}}, got)
    single = {"package.json": '{ "name": "mono" }\n', "apps/api/src/main.ts": _nest_main("  app.setGlobalPrefix('api');\n"),
              "libs/users/src/lib/users.controller.ts": _nest_ctrl("UsersController", "users")}
    nx1 = dict(single, **{"nx.json": "{}\n", "apps/api/project.json": '{ "name": "api", "projectType": "application" }\n',
                          "libs/users/project.json": '{ "name": "users", "projectType": "library" }\n'})
    one = {"nx": nx1,
           "workspace": dict(single, **{"angular.json": ('{ "projects": { "api": { "root": "apps/api" }, '
                                                         '"users": { "root": "libs/users" } } }\n')}),
           "nest-cli": dict(single, **{"nest-cli.json": ('{ "monorepo": true, "projects": { "api": { "type": "application", '
                                                         '"root": "apps/api" }, "users": { "type": "library", '
                                                         '"root": "libs/users" } } }\n')}),
           "two apps": dict(nx1, **{"apps/admin/project.json": '{ "name": "admin" }\n',
                                    "apps/admin/src/main.ts": _nest_main("  app.setGlobalPrefix('admin');\n"),
                                    "apps/admin/src/admin.controller.ts": _nest_ctrl("AdminController", "admin")})}
    got = {name: {x.src_file: (x.attrs.get("path"), x.attrs.get("global_prefix"))
                  for x in _tree_edges(base, "gp_one_" + name.replace(" ", "_"), files)} for name, files in one.items()}
    lib = "libs/users/src/lib/users.controller.ts"
    check("with ONE HTTP bootstrap in the tree no project bound (Nx project.json, angular.json / workspace.json, "
          "nest-cli.json `projects`) cuts the walk: the only app's global prefix reaches its library's controllers; with "
          "two, each app stays in its project and the library gets none",
          got == {"nx": {lib: ("/api/users", "api")}, "workspace": {lib: ("/api/users", "api")},
                  "nest-cli": {lib: ("/api/users", "api")},
                  "two apps": {lib: ("/users", None), "apps/admin/src/admin.controller.ts": ("/admin/admin", "admin")}}, got)
    # ...as the generators write them: the app's own directory holds a controller too
    ext = '{ "extends": "../../tsconfig.base.json" }\n'
    nx_base = {"package.json": '{ "name": "nx-mono" }\n', "nx.json": "{}\n",
               "tsconfig.base.json": ('{\n  "compilerOptions": {\n    "baseUrl": ".",\n    "paths": {\n'
                                      '      "@nx-mono/users": ["libs/users/src/index.ts"]\n    }\n  }\n}\n'),
               "apps/api/project.json": '{ "name": "api", "projectType": "application", "sourceRoot": "apps/api/src" }\n',
               "apps/api/tsconfig.json": ext,
               "apps/api/src/main.ts": _nest_main("  app.setGlobalPrefix('api');\n").replace("./app.module", "./app/app.module"),
               "apps/api/src/app/app.module.ts": ("import { Module } from '@nestjs/common';\nimport { UsersModule } from "
                                                  "'@nx-mono/users';\n\n@Module({ imports: [UsersModule] })\nexport class AppModule {}\n"),
               "libs/users/project.json": '{ "name": "users", "projectType": "library", "sourceRoot": "libs/users/src" }\n',
               "libs/users/tsconfig.json": ext,
               "libs/users/src/index.ts": "export * from './lib/users.module';\nexport * from './lib/users.controller';\n",
               "libs/users/src/lib/users.module.ts": ("import { Module } from '@nestjs/common';\nimport { UsersController } from "
                                                      "'./users.controller';\n\n@Module({ controllers: [UsersController] })\n"
                                                      "export class UsersModule {}\n"),
               "libs/users/src/lib/users.controller.ts": ("import { Controller, Get } from '@nestjs/common';\n\n@Controller('users')\n"
                                                          "export class UsersController {\n  @Get(':id')\n  findOne() {\n"
                                                          "    return {};\n  }\n}\n")}
    nx_gen = dict(nx_base, **{    # the files `nx g @nx/nest:app api` and `nx g @nx/nest:lib users --controller` write
        "apps/api/src/main.ts": ("/**\n * This is not a production server yet!\n * This is only a minimal backend to get started.\n"
                                 " */\n\nimport { Logger } from '@nestjs/common';\nimport { NestFactory } from '@nestjs/core';\n\n"
                                 "import { AppModule } from './app/app.module';\n\nasync function bootstrap() {\n"
                                 "  const app = await NestFactory.create(AppModule);\n  const globalPrefix = 'api';\n"
                                 "  app.setGlobalPrefix(globalPrefix);\n  const port = process.env.PORT || 3000;\n"
                                 "  await app.listen(port);\n  Logger.log(\n"
                                 "    `Application is running on: http://localhost:${port}/${globalPrefix}`\n  );\n}\n\nbootstrap();\n"),
        "apps/api/src/app/app.module.ts": ("import { Module } from '@nestjs/common';\nimport { UsersModule } from '@nx-mono/users';\n\n"
                                           "import { AppController } from './app.controller';\nimport { AppService } from './app.service';\n\n"
                                           "@Module({\n  imports: [UsersModule],\n  controllers: [AppController],\n"
                                           "  providers: [AppService],\n})\nexport class AppModule {}\n"),
        "apps/api/src/app/app.controller.ts": ("import { Controller, Get } from '@nestjs/common';\n\n"
                                               "import { AppService } from './app.service';\n\n@Controller()\n"
                                               "export class AppController {\n  constructor(private readonly appService: AppService) {}\n\n"
                                               "  @Get()\n  getData() {\n    return this.appService.getData();\n  }\n}\n"),
        "apps/api/src/app/app.service.ts": ("import { Injectable } from '@nestjs/common';\n\n@Injectable()\nexport class AppService {\n"
                                            "  getData(): { message: string } {\n    return { message: 'Hello API' };\n  }\n}\n"),
        "libs/users/src/lib/users.controller.ts": ("import { Controller, Get, Param } from '@nestjs/common';\n"
                                                   "import { UsersService } from './users.service';\n\n@Controller('users')\n"
                                                   "export class UsersController {\n  constructor(private usersService: UsersService) {}\n\n"
                                                   "  @Get(':id')\n  findOne(@Param('id') id: string) {\n"
                                                   "    return this.usersService.findOne(id);\n  }\n}\n"),
        "libs/users/src/lib/users.service.ts": ("import { Injectable } from '@nestjs/common';\n\n@Injectable()\nexport class UsersService {\n"
                                                "  findOne(id: string) {\n    return { id };\n  }\n}\n")})
    nest_cli = {"package.json": '{ "name": "nest-mono" }\n',
                "nest-cli.json": ('{ "monorepo": true, "root": "apps/api", "projects": { "api": { "type": "application", '
                                  '"root": "apps/api" }, "common": { "type": "library", "root": "libs/common" } } }\n'),
                "tsconfig.json": ('{\n  "compilerOptions": {\n    "baseUrl": "./",\n    "paths": {\n'
                                  '      "@app/common": ["libs/common/src"],\n      "@app/common/*": ["libs/common/src/*"]\n'
                                  '    }\n  }\n}\n'),
                "apps/api/src/main.ts": _nest_main("  app.setGlobalPrefix('api');\n").replace("AppModule", "ApiModule")
                                                                                    .replace("./app.module", "./api.module"),
                "apps/api/src/api.module.ts": ("import { Module } from '@nestjs/common';\nimport { CommonModule } from '@app/common';\n"
                                               "import { ApiController } from './api.controller';\n\n"
                                               "@Module({ imports: [CommonModule], controllers: [ApiController] })\n"
                                               "export class ApiModule {}\n"),
                "apps/api/src/api.controller.ts": ("import { Controller, Get } from '@nestjs/common';\n\n@Controller()\n"
                                                   "export class ApiController {\n  @Get()\n  getHello() {\n    return 'hi';\n  }\n}\n"),
                "libs/common/src/index.ts": "export * from './common.module';\nexport * from './health.controller';\n",
                "libs/common/src/common.module.ts": ("import { Module } from '@nestjs/common';\n"
                                                     "import { HealthController } from './health.controller';\n\n"
                                                     "@Module({ controllers: [HealthController] })\nexport class CommonModule {}\n"),
                "libs/common/src/health.controller.ts": ("import { Controller, Get } from '@nestjs/common';\n\n@Controller('health')\n"
                                                         "export class HealthController {\n  @Get()\n  check() {\n"
                                                         "    return 'ok';\n  }\n}\n")}
    worker = dict(nx_base, **{    # a second Nx APPLICATION whose bootstrap goes through a package the tree lacks
        "apps/worker/project.json": '{ "name": "worker", "projectType": "application" }\n',
        "apps/worker/src/main.ts": ("import { startApp } from '@org/platform';\nimport { WorkerModule } from './worker.module';\n"
                                    "startApp(WorkerModule);\n"),
        "apps/worker/src/worker.module.ts": ("import { Module } from '@nestjs/common';\nimport { JobsController } from "
                                             "'./jobs.controller';\n\n@Module({ controllers: [JobsController] })\n"
                                             "export class WorkerModule {}\n"),
        "apps/worker/src/jobs.controller.ts": ("import { Controller, Get } from '@nestjs/common';\n\n@Controller('jobs')\n"
                                               "export class JobsController {\n  @Get()\n  list() {\n    return [];\n  }\n}\n")})
    got = {name: {x.src_file: (x.attrs.get("path"), x.attrs.get("global_prefix"))
                  for x in _e(_tree_edges(base, "gp_gen_" + name.replace("-", "_"), files), "ROUTES_TO")}
           for name, files in (("nx", nx_gen), ("nest-cli", nest_cli), ("worker", worker))}
    check("...also as the generators write it, the app's own directory holding a controller too (the files `nx g "
          "@nx/nest:app api` + `nx g @nx/nest:lib users --controller` write, main.ts's `const globalPrefix = 'api'` "
          "verbatim; a Nest CLI monorepo's apps/api/src/api.controller.ts): the library's controller gets the one app's "
          "prefix; another APPLICATION project (an Nx project.json `projectType: application`) whose bootstrap the "
          "reader cannot see (`startApp(WorkerModule)` from a package) gets none, its library still the one app's",
          got == {"nx": {"apps/api/src/app/app.controller.ts": ("/api", "api"),
                         "libs/users/src/lib/users.controller.ts": ("/api/users/:id", "api")},
                  "nest-cli": {"apps/api/src/api.controller.ts": ("/api", "api"),
                               "libs/common/src/health.controller.ts": ("/api/health", "api")},
                  "worker": {"apps/worker/src/jobs.controller.ts": ("/jobs", None),
                             "libs/users/src/lib/users.controller.ts": ("/api/users/:id", "api")}}, got)
    buildable = dict(nx_gen, **{"libs/users/package.json": '{ "name": "@nx-mono/users", "version": "0.0.1" }\n'})
    nest_worker = dict(nest_cli, **{
        "nest-cli.json": ('{ "monorepo": true, "root": "apps/api", "projects": { "api": { "type": "application", '
                          '"root": "apps/api" }, "worker": { "type": "application", "root": "apps/worker" }, '
                          '"common": { "type": "library", "root": "libs/common" } } }\n'),
        "apps/worker/src/main.ts": worker["apps/worker/src/main.ts"],
        "apps/worker/src/worker.module.ts": worker["apps/worker/src/worker.module.ts"],
        "apps/worker/src/jobs.controller.ts": worker["apps/worker/src/jobs.controller.ts"]})
    got = {name: {x.src_file: (x.attrs.get("path"), x.attrs.get("global_prefix"))
                  for x in _e(_tree_edges(base, "gp_gen_" + name.replace("-", "_"), files), "ROUTES_TO")}
           for name, files in (("buildable", buildable), ("nest-cli-worker", nest_worker))}
    check("...with one app, a library in a package of its own (an Nx buildable library's package.json) keeps its routes "
          "unprefixed, never claimed across a package boundary; a second application nest-cli.json declares (`type: "
          "application`) whose bootstrap the reader cannot see gets none either, the library beside it the one app's",
          got == {"buildable": {"apps/api/src/app/app.controller.ts": ("/api", "api"),
                                "libs/users/src/lib/users.controller.ts": ("/users/:id", None)},
                  "nest-cli-worker": {"apps/api/src/api.controller.ts": ("/api", "api"),
                                      "apps/worker/src/jobs.controller.ts": ("/jobs", None),
                                      "libs/common/src/health.controller.ts": ("/api/health", "api")}}, got)
    crud = {"src/main.ts": _nest_main("  app.setGlobalPrefix('api');\n"),
            "src/common/crud.controller.ts": ("import { Get, Post } from '@nestjs/common';\n\n"
                                              "export abstract class CrudController {\n  @Get(':id')\n  findOne() {}\n"
                                              "  @Post()\n  create() {}\n}\n"),
            "src/cats.controller.ts": _nest_ctrl("CatsController", "cats")}
    got = [(x.dst_name, x.attrs.get("path"), x.attrs.get("prefix_unresolved"), x.attrs.get("global_prefix"))
           for x in _e(_tree_edges(base, "crud", crud), "ROUTES_TO")]
    check("route decorators on a class with no @Controller: the method's own path, the prefix marked unresolved "
          "(each subclass's), no global prefix; a @Controller class beside it is unaffected",
          sorted(got) == [("CatsController.all", "/api/cats", None, "api"),
                          ("CrudController.create", "/", NO_CONTROLLER, None),
                          ("CrudController.findOne", "/:id", NO_CONTROLLER, None)], got)
    sse = ("import { Controller, Get, Sse, Search } from '@nestjs/common';\n\n"
           "@Controller('events')\nexport class EventsController {\n  @Sse('stream')\n  stream() {}\n"
           "  @Search()\n  find() {}\n  @Get()\n  all() {}\n}\n")
    other = ("import { Controller } from '@nestjs/common';\nimport { Search, Sse } from 'search-kit';\n\n"
             "@Controller('idx')\nexport class IndexController {\n  @Search()\n  reindex() {}\n  @Sse()\n  feed() {}\n}\n")
    got = [[(x.dst_name, x.attrs.get("method"), x.attrs.get("path")) for x in _e(T.recognize(src, rel)[1], "ROUTES_TO")]
           for src, rel in ((sse, "src/ev.ts"), (other, "src/idx.ts"))]
    check("NestJS @Sse('stream') is a GET route and @Search() a SEARCH route; a @Search / @Sse imported from "
          "another package is no route",
          got == [[("EventsController.stream", "GET", "/events/stream"), ("EventsController.find", "SEARCH", "/events"),
                   ("EventsController.all", "GET", "/events")], []], got)

    # ---- mounts across files: NodeNext `.js` specifiers, aliases, koa nesting, Fastify inline plugins, tests
    nodenext = {"src/app.ts": ("import express from 'express';\nimport usersRouter from './routes/users.js';\n"
                               "const app = express();\napp.use('/api/users', usersRouter);\nexport default app;\n"),
                "src/routes/users.ts": ("import { Router } from 'express';\nimport { listUsers } from '../controllers/users.js';\n"
                                        "const router = Router();\nrouter.get('/', listUsers);\nexport default router;\n"),
                "src/controllers/users.ts": "export function listUsers() {}\n"}
    got = _rt(_tree_edges(base, "nodenext", nodenext))
    check("a NodeNext `./routes/users.js` specifier names users.ts: the mount composes /api/users (and the "
          "handler's `../controllers/users.js` names users.ts)",
          got == [("src/routes/users.ts", "listUsers", "GET", "/api/users", None, None,
                   "L0,1|exact:module:src/controllers/users")], got)
    aliased = {"src/app.ts": ("import express from 'express';\nimport usersRouter from '@/routes/users';\n"
                              "import ordersRouter from '@/routes/orders';\nconst app = express();\n"
                              "app.use('/api/users', usersRouter);\napp.use('/api/orders', ordersRouter);\n"),
               "src/routes/users.ts": ("import { Router } from 'express';\nconst router = Router();\nfunction list() {}\n"
                                       "router.get('/', list);\nexport default router;\n"),
               "src/routes/orders.ts": ("import { Router } from 'express';\nconst router = Router();\nfunction list() {}\n"
                                        "router.get('/', list);\nexport default router;\n"),
               "apps/b/src/routes/orders.ts": ("import { Router } from 'express';\nconst router = Router();\n"
                                               "function list() {}\nrouter.get('/b', list);\nexport default router;\n")}
    got = {x.src_file: x.attrs.get("path") for x in _tree_edges(base, "alias_mount", aliased)}
    check("a mount imported through an alias (`@/routes/users`) composes when exactly one module matches; an "
          "alias two modules match is left uncomposed (never a guessed prefix)",
          got == {"src/routes/users.ts": "/api/users", "src/routes/orders.ts": "/", "apps/b/src/routes/orders.ts": "/b"},
          got)
    barrels = {"tsconfig.json": '{ "compilerOptions": { "baseUrl": ".", "paths": { "~/*": ["./src/*"] } } }\n',
               "src/routes/index.ts": ("export { default as Users } from './user.routes';\nexport * from './orders';\n"
                                       "export { postsRouter as Posts } from './posts';\n"),
               "src/routes/user.routes.ts": ("import { Hono } from 'hono';\nconst users = new Hono();\n"
                                             "function getUsers(c) {}\nfunction login(c) {}\n"
                                             "users.get('/', getUsers);\nusers.post('/login', login);\nexport default users;\n"),
               "src/routes/orders.ts": ("import { Hono } from 'hono';\nexport const ordersRouter = new Hono();\n"
                                        "function listOrders(c) {}\nordersRouter.get('/', listOrders);\n"),
               "src/routes/posts.ts": ("import { Hono } from 'hono';\nconst postsRouter = new Hono();\nfunction listPosts(c) {}\n"
                                       "postsRouter.get('/', listPosts);\nexport { postsRouter };\n"),
               "src/server.ts": ("import { Hono } from 'hono';\nimport { Users, ordersRouter, Posts } from '~/routes';\n"
                                 "const app = new Hono().basePath('/api/v1');\napp.route('/users', Users);\n"
                                 "app.route('/orders', ordersRouter);\napp.route('/posts', Posts);\nexport default app;\n")}
    got = {x.dst_name: x.attrs.get("path") for x in _tree_edges(base, "mount_barrels", barrels)}
    check("a router imported through a barrel (`export { default as Users } from './user.routes'`, `export * from "
          "'./orders'`, `export { postsRouter as Posts } from './posts'`) is mounted where the barrel's import is: "
          "/api/v1/users..., /api/v1/orders, /api/v1/posts",
          got == {"getUsers": "/api/v1/users", "login": "/api/v1/users/login", "listOrders": "/api/v1/orders",
                  "listPosts": "/api/v1/posts"}, got)
    plug = {"src/plugins/index.ts": "export { default as items } from './items';\n",
            "src/plugins/items.ts": ("import { FastifyInstance } from 'fastify';\nasync function listItems() {}\n"
                                     "export default async function (fastify: FastifyInstance) {\n"
                                     "  fastify.get('/', listItems);\n}\n"),
            "src/server.ts": ("import Fastify from 'fastify';\nimport { items } from './plugins';\nconst app = Fastify();\n"
                              "app.register(items, { prefix: '/items' });\n")}
    got = [(x.dst_name, x.attrs.get("path")) for x in _tree_edges(base, "plugin_barrel", plug)]
    check("...and a Fastify plugin that is an anonymous default export, registered through such a barrel, gets its "
          "register prefix: /items", got == [("listItems", "/items")], got)
    star_plug = {"src/plugins/index.ts": "export * from './group';\n",
                 "src/plugins/group.ts": "export { default as health } from './health';\n",
                 "src/plugins/health.ts": ("import { check } from '../handlers';\nexport default async function (fastify, opts) {\n"
                                           "  fastify.get('/health', check);\n}\n"),
                 "src/handlers.ts": "export async function check(req, reply) {}\n",
                 "src/app.ts": ("import Fastify from 'fastify';\nimport { health } from './plugins';\nconst app = Fastify();\n"
                                "app.register(health, { prefix: '/internal' });\n")}
    got = [(x.dst_name, x.attrs.get("path")) for x in _tree_edges(base, "plugin_star", star_plug)]
    check("...also behind an `export *` chain (index -> `export * from './group'` -> `export { default as health } "
          "from './health'`): /internal/health", got == [("check", "/internal/health")], got)
    chained_plug = {"src/app.js": ("const Fastify = require('fastify');\nconst app = Fastify();\n"
                                   "app.register(require('./plugin'), { prefix: '/v1' });\n"
                                   "app.register(require('./plugin2'), { prefix: '/v2' });\n"),
                    "src/plugin.js": ("module.exports = exports = async function (fastify, opts) {\n"
                                      "  fastify.get('/items', listItems);\n};\nfunction listItems(req, reply) {}\n"),
                    "src/plugin2.js": ("module.exports = async function (fastify, opts) {\n  fastify.get('/items2', listItems2);\n"
                                       "};\nfunction listItems2(req, reply) {}\n"),
                    "src/routes3.js": ("const express = require('express');\nmodule.exports = exports = function (app) {\n"
                                       "  app.get('/things', listThings);\n};\nfunction listThings(req, res) {}\n")}
    got = sorted((x.dst_name, x.attrs.get("path")) for x in _tree_edges(base, "plugin_chained", chained_plug))
    check("...and a CommonJS plugin written with the legacy chain `module.exports = exports = async function (fastify, "
          "opts)` is that anonymous default too: register(require('./plugin'), { prefix: '/v1' }) gives /v1/items, as "
          "its unchained twin gets /v2/items2 (a chained `function (app)` nothing mounts keeps /things)",
          got == [("listItems", "/v1/items"), ("listItems2", "/v2/items2"), ("listThings", "/things")], got)
    users_r = ("import { Router } from 'express';\nfunction list(req, res) {}\nconst usersRouter = Router();\n"
               "usersRouter.get('/', list);\nexport default usersRouter;\n")
    bare = {"tsconfig.json": '{ "compilerOptions": { "baseUrl": ".", "paths": { "routes/*": ["src/routes/*"] } } }\n',
            "src/routes/users.ts": users_r,
            "admin/routes.ts": users_r.replace("usersRouter", "adminRouter"),
            "packages/users-api/package.json": '{ "name": "users-api", "main": "src/index.ts" }\n',
            "packages/users-api/src/index.ts": users_r.replace("usersRouter", "pkgRouter"),
            "src/app.ts": ("import express from 'express';\nimport cors from 'cors';\nimport usersRouter from 'routes/users';\n"
                           "import adminRouter from 'admin/routes';\nimport pkgRouter from 'users-api';\nconst app = express();\n"
                           "app.use('/api/users', usersRouter);\napp.use('/admin', adminRouter);\napp.use('/pkg', pkgRouter);\n"
                           "app.use('/x', cors);\n")}
    got = {x.src_file: x.attrs.get("path") for x in _tree_edges(base, "bare_mounts", bare)}
    check("a router mounted through a bare specifier composes when finalize places it in the tree: a tsconfig `paths` "
          "key with no @ / ~ / # prefix (`routes/users`), a `baseUrl` name (`admin/routes`), an unscoped workspace "
          "package (`users-api`); a package the tree does not hold (`cors`) mounts nothing",
          got == {"src/routes/users.ts": "/api/users", "admin/routes.ts": "/admin",
                  "packages/users-api/src/index.ts": "/pkg"}, got)
    koa = {"src/api.js": ("const Router = require('@koa/router');\nconst users = require('./users');\n"
                          "const api = new Router({ prefix: '/api' });\n"
                          "api.use('/users', users.routes(), users.allowedMethods());\nmodule.exports = api;\n"),
           "src/users.js": ("const Router = require('@koa/router');\nconst users = new Router();\n"
                            "async function list(ctx) {}\nusers.get('/', list);\nmodule.exports = users;\n")}
    got = [x.attrs.get("path") for x in _tree_edges(base, "koa_nest", koa)]
    check("koa-router nesting `api.use('/users', users.routes(), users.allowedMethods())` composes /api/users",
          got == ["/api/users"], got)
    many = {"src/app.js": ("const express = require('express');\nconst auth = require('./auth');\n"
                           "const api = require('./api');\nconst { limit } = require('./limit');\nconst app = express();\n"
                           "app.use('/v1', limit, auth, api);\n"),
            "src/auth.js": ("const router = require('express').Router();\nfunction login(req, res) {}\n"
                            "router.post('/login', login);\nmodule.exports = router;\n"),
            "src/api.js": ("const router = require('express').Router();\nfunction ping(req, res) {}\n"
                           "router.get('/ping', ping);\nmodule.exports = router;\n"),
            "src/limit.js": "function limit(req, res, next) { next(); }\nmodule.exports = { limit };\n"}
    got = {x.dst_name: x.attrs.get("path") for x in _tree_edges(base, "use_many", many)}
    check("`app.use('/v1', limit, auth, api)`: every router operand is mounted at /v1 (a middleware function "
          "is no mount)", got == {"login": "/v1/login", "ping": "/v1/ping"}, got)
    inline = {"src/server.ts": ("import Fastify from 'fastify';\nimport { listUsers, getUser } from './handlers';\n"
                                "const server = Fastify();\n"
                                "server.register((instance, opts, done) => {\n  instance.get('/users', listUsers);\n"
                                "  instance.get('/users/:id', getUser);\n  done();\n}, { prefix: '/v1' });\n"
                                "server.register(async function (api) {\n  api.get('/health', listUsers);\n"
                                "}, { prefix: '/internal' });\n"
                                "container.register((c) => {\n  c.get('/svc', listUsers);\n});\n"),
              "src/handlers.ts": "export async function listUsers() {}\nexport async function getUser() {}\n"}
    got = sorted((x.dst_name, x.attrs.get("path"), x.attrs.get("routes")) for x in _tree_edges(base, "fastify_inline",
                                                                                              inline))
    check("Fastify plugins registered inline (`server.register((instance) => {...}, { prefix: '/v1' })`, an "
          "`async function (api)`) compose their prefix; a register() on something else adds no route",
          got == [("getUser", "/v1/users/:id", None),
                  ("listUsers", "/v1/users", ["GET /internal/health", "GET /v1/users"])], got)
    prod_test = {"src/app.js": ("const express = require('express');\nconst usersRouter = require('./routes/users');\n"
                                "const app = express();\napp.use('/api/v1/users', usersRouter);\n"),
                 "src/routes/users.js": ("const express = require('express');\nconst router = express.Router();\n"
                                         "function listUsers(req, res) {}\nrouter.get('/', listUsers);\n"
                                         "module.exports = router;\n"),
                 "test/users.test.js": ("const express = require('express');\n"
                                        "const usersRouter = require('../src/routes/users');\n"
                                        "const app = express();\napp.use(usersRouter);\n"),
                 "test/helpers/router.js": ("const express = require('express');\nconst router = express.Router();\n"
                                            "function stub(req, res) {}\nrouter.get('/x', stub);\nmodule.exports = router;\n"),
                 "test/helpers/app.test.js": ("const express = require('express');\nconst stubs = require('./router');\n"
                                              "const app = express();\napp.use('/t', stubs);\n")}
    got = {x.dst_name: (x.attrs.get("path"), x.attrs.get("paths")) for x in _tree_edges(base, "test_mount", prod_test)}
    check("a test file's mount never shapes a production router's path (/api/v1/users, no test path in `paths`); "
          "a router only tests mount keeps that mount",
          got == {"listUsers": ("/api/v1/users", None), "stub": ("/t/x", None)}, got)

    # ---- small route shapes
    shorthand = ("import Fastify from 'fastify';\nconst fastify = Fastify();\n"
                 "const handler = async (req, reply) => {\n  return 1;\n};\nconst url = '/c';\n"
                 "fastify.route({ method: 'GET', url: '/a', handler });\nfastify.route({ method: 'POST', url, handler });\n")
    got = [(x.dst_name, x.conf_hint, x.attrs.get("method"), x.attrs.get("path"))
           for x in _e(T.recognize(shorthand, "src/server.ts")[1], "ROUTES_TO")]
    check("fastify.route({ method, url, handler }) with shorthand properties: `handler` is the handler, a "
          "shorthand `url` names its const", got == [("handler", "LOCAL", "GET", "/a"), ("handler", "LOCAL", "POST", "/c")],
          got)
    ng = {"src/app/admin/admin.module.ts": (
        "import { NgModule } from '@angular/core';\nimport { RouterModule } from '@angular/router';\n"
        "import { AdminComponent } from './admin.component';\n"
        "const routes = [{ path: 'admin', component: AdminComponent }];\n"
        "@NgModule({ imports: [RouterModule.forChild(routes)] })\nexport class AdminModule {}\n"),
          "src/app/store.module.ts": (
        "import { NgModule } from '@angular/core';\nimport { StoreModule } from '@ngrx/store';\n"
        "import { PanelComponent } from './panel.component';\n"
        "const routes = [{ path: 'panel', component: PanelComponent }];\n"
        "@NgModule({ imports: [StoreModule.forRoot(routes)] })\nexport class AppStoreModule {}\n"),
          "src/app/local.module.ts": (
        "import { NgModule } from '@angular/core';\nimport { RouterModule } from './my-router';\n"
        "import { PanelComponent } from './panel.component';\n"
        "const routes = [{ path: 'panel', component: PanelComponent }];\n"
        "@NgModule({ imports: [RouterModule.forChild(routes)] })\nexport class LocalModule {}\n")}
    got = [(x.src_file, x.dst_name, x.attrs.get("path")) for x in _e(_tree_edges(base, "ng_forchild", ng), "ROUTES_TO")]
    check("Angular: an unannotated `const routes = [...]` passed to RouterModule.forChild(routes) is read; the "
          "same array passed to another module's forRoot, or to a RouterModule not from @angular/router, is not",
          got == [("src/app/admin/admin.module.ts", "AdminComponent", "/admin")], got)
    nxt = {"apps/app/next.config.ts": "export default {};\n",
           "apps/app/app/api/collaboration/auth/route.ts": "export async function POST() {\n  return new Response('');\n}\n",
           "apps/app/lib/app/route.ts": "export async function GET() {\n  return new Response('');\n}\n",
           "apps/api/package.json": '{ "name": "api", "dependencies": { "next": "15.0.0" } }\n',
           "apps/api/app/health/route.ts": "export async function GET() {\n  return new Response('');\n}\n",
           "apps/api/src/app/ignored/route.ts": "export async function GET() {\n  return new Response('');\n}\n",
           "apps/site/next.config.mjs": "export default {};\n",
           "apps/site/src/app/api/ping/route.ts": "export async function GET() {\n  return new Response('');\n}\n",
           "app/web/app/api/users/route.ts": "export async function GET() {\n  return new Response('');\n}\n"}
    got = {x.src_file: x.attrs.get("path") for x in _tree_edges(base, "next_root", nxt)}
    check("Next.js: the app router is the `app` (else `src/app`) directory of the project root (next.config, or "
          "a package.json depending on next): apps/app/app -> /api/collaboration/auth; a route.ts elsewhere under "
          "the root, or in src/app beside a root app/ (Next ignores it), is no route; with no root marker the "
          "old rule stays",
          got == {"apps/app/app/api/collaboration/auth/route.ts": "/api/collaboration/auth",
                  "apps/api/app/health/route.ts": "/health",
                  "apps/site/src/app/api/ping/route.ts": "/api/ping",
                  "app/web/app/api/users/route.ts": "/web/app/api/users"}, got)
    pkg = {"package.json": '{ "devDependencies": { "next": "15.0.0" } }\n',
           "frontend/app/api/x/route.ts": "export async function GET() {\n  return new Response('');\n}\n"}
    got = {x.src_file: x.attrs.get("path") for x in _tree_edges(base, "next_pkg", pkg)}
    check("...a package.json depending on next marks a root only beside an app / pages directory (a monorepo "
          "root that lists next keeps a sub-folder app's routes)", got == {"frontend/app/api/x/route.ts": "/api/x"},
          got)

    # ---- path folding reads a binding only where it is in scope
    param = ("import express from 'express';\nimport { getUser, health } from './handlers';\n\n"
             "const base = '/api';\nconst router = express.Router();\n\n"
             "export function registerAdmin(router: express.Router, base: string) {\n"
             "  router.get(`${base}/users/:id`, getUser);\n}\n\n"
             "router.get(`${base}/health`, health);\nexport default router;\n")
    got = [(x.dst_name, x.attrs.get("path"), x.attrs.get("path_unresolved"))
           for x in _e(T.recognize(param, "src/server.ts")[1], "ROUTES_TO")]
    check("a parameter that shares a module const's name is never folded to the const (`base` -> unresolved)",
          got == [("getUser", None, "`${base}/users/:id`"), ("health", None, "`${base}/health`")], got)
    destr = ("const express = require('express');\nconst { b } = require('./h');\n\n"
             "const router = express.Router();\nconst prefix = '/public';\n\n"
             "function mount({ prefix }) {\n  router.get(`${prefix}/b`, b);\n}\n\nmodule.exports = router;\n")
    other_fn = ("import { Router } from 'express';\nimport { list } from './h';\n\n"
                "const router = Router();\nconst PREFIX = '/p';\n\n"
                "function legacy() {\n  const OLD = '/legacy';\n  return OLD;\n}\n\n"
                "export function mount(PREFIX: string) {\n  router.get(`${PREFIX}/users`, list);\n}\n\n"
                "export function mount2() {\n  router.get(`${OLD}/orders`, list);\n}\n\n"
                "export function mount3() {\n  const LOCAL = '/local';\n  router.get(`${LOCAL}/x`, list);\n}\n")
    arrow = ("const express = require('express');\nconst { b } = require('./h');\n\n"
             "const router = express.Router();\nconst tag = '/t';\n\n"
             "['/x'].forEach(tag => router.get(`${tag}/y`, b));\n\nmodule.exports = router;\n")
    heads = ("import { Router } from 'express';\nimport { list } from './h';\n\n"
             "const router = Router();\nconst V = '/v';\n\n"
             "if (V) {\n  router.get(`${V}/x`, list);\n}\nfor (const k of [1]) {\n  while (k) {\n    break;\n  }\n}\n"
             "switch (V) {\n  default:\n    router.get(`${V}/y`, list);\n}\n")
    decl = ("const express = require('express');\nconst { b } = require('./h');\n\n"
            "const router = express.Router();\nconst prefix = '/public';\n\n"
            "function mount(opts) {\n  const { prefix } = opts;\n  router.get(`${prefix}/c`, b);\n}\n\n"
            "module.exports = router;\n")
    got = [[x.attrs.get("path") for x in _e(T.recognize(src, rel)[1], "ROUTES_TO")]
           for src, rel in ((destr, "src/routes.js"), (other_fn, "src/routes.ts"), (arrow, "src/arrow.js"),
                            (heads, "src/heads.ts"), (decl, "src/decl.js"))]
    check("a destructured, plain or one-parameter-arrow parameter, or a destructuring declaration, that shares a "
          "const's name is never folded to the const; a const declared inside one function is not in scope in "
          "another — but folds inside its own function; an `if` / `switch` head binds nothing",
          got == [[None], [None, None, "/local/x"], [None], ["/v/x", "/v/y"], [None]], got)
    head = "import { Router } from 'express';\nconst router = Router();\nfunction h(req, res) {}\n"
    shapes = (("a for-of loop variable", "const prefix = '/api';\nfor (const prefix of ['/v1', '/v2']) {\n"
                                         "  router.get(prefix, h);\n}\n"),
              ("a for-in loop variable", "const v = '/api';\nfor (const v in { a: 1 }) {\n  router.get(`/${v}/users`, h);\n}\n"),
              ("a `let` with no initializer", "const base = '/api';\nfunction mount(flag) {\n  let base;\n"
                                              "  base = flag ? '/v1' : '/v2';\n  router.get(`${base}/users`, h);\n}\n"),
              ("a `var` with no initializer", "const base = '/api';\nfunction mount() {\n  var base;\n  router.get(base, h);\n}\n"),
              ("a later declarator", "const base = '/api';\nfunction mount() {\n  const a = 1, base = '/v2';\n"
                                     "  router.get(`${base}/users`, h);\n}\n"),
              ("a destructuring pattern later in a list", "const base = '/api';\nfunction mount(opts) {\n"
                                                          "  const a = 1, { base } = opts;\n"
                                                          "  router.get(`${base}/users`, h);\n}\n"))
    got = {desc: [(x.attrs.get("path"), bool(x.attrs.get("path_unresolved"))) for x in
                  _e(T.recognize(head + src, "src/r.ts")[1], "ROUTES_TO")] for desc, src in shapes}
    check("a module const shadowed by a for-of / for-in loop variable, a `let` / `var` with no initializer or a later "
          "declarator (a destructuring pattern included) of another statement is never folded (path unresolved, "
          "never the module const's '/api')", all(v == [(None, True)] for v in got.values()), got)
    multi = head + "const API = '/api', V2 = '/v2';\nrouter.get(`${API}${V2}/users`, h);\n"
    got = [x.attrs.get("path") for x in _e(T.recognize(multi, "src/r.ts")[1], "ROUTES_TO")]
    check("...while each declarator of `const API = '/api', V2 = '/v2'` holds its own value: /api/v2/users",
          got == ["/api/v2/users"], got)
    loop = {"src/app.js": ("const express = require('express');\nconst users = require('./users');\nconst app = express();\n"
                           "const prefix = '/api';\nfor (const prefix of ['/v1', '/v2']) {\n  app.use(prefix, users);\n}\n"),
            "src/users.js": ("const express = require('express');\nconst router = express.Router();\n"
                             "function list(req, res) {}\nrouter.get('/users', list);\nmodule.exports = router;\n")}
    got = [(x.attrs.get("path"), x.attrs.get("prefix_unresolved")) for x in _tree_edges(base, "mount_forof", loop)]
    check("a mount inside `for (const prefix of [...])` is a dynamic mount (prefix unresolved), never the shadowed "
          "module const's '/api'", got == [("/users", "<dynamic mount prefix>")], got)
    field = ("import { Router } from 'express';\nimport { UsersController } from './users.controller';\n\n"
             "export class UsersRoute {\n  public path = '/';\n  public base = '/b';\n  public router = Router();\n"
             "  private users = new UsersController();\n\n  constructor(path: string) {\n    this.path = path;\n"
             "    this.router.get(`${this.path}/:id`, this.users.getOne);\n"
             "    this.router.get(`${this.base}/all`, this.users.getAll);\n  }\n}\n")
    got = [(x.attrs.get("path"), x.attrs.get("path_unresolved"))
           for x in _e(T.recognize(field, "src/users.route.ts")[1], "ROUTES_TO")]
    check("a field the class assigns again (`this.path = path` in the constructor) is not folded to its "
          "initializer; a field never reassigned is",
          got == [(None, "`${this.path}/:id`"), ("/b/all", None)], got)


# Transpiler output: the same classes as their TypeScript source, so never read (graphify skips an ignored lib/).
TSC_CJS = ('"use strict";\nObject.defineProperty(exports, "__esModule", { value: true });\nexports.BaseService = void 0;\n'
           "class BaseService {\n}\nexports.BaseService = BaseService;\n")
TSC_ESM = ("var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) { return target; };\n"
           'import { Injectable } from "@nestjs/common";\nexport class Repo extends Store {\n}\n')
MAPPED_JS = "export class Mapped extends Store {\n}\n//# sourceMappingURL=mapped.js.map\n"


def gate_reader_precision() -> None:
    print("\n-- R5: the reader's precision: the lexer, transpiler output, imported names, ORM shapes, handlers --")
    if T is None:
        check("build/typescript_edges.py exists (the recognizer)", False, "ImportError")
        return
    # ---- the lexer: a regex after an `if (...)` head, a postfix ++ before a division, a backtick in JSX text
    rx = ("import { Router } from 'express';\nimport { list } from './h';\nconst router = Router();\n"
          "function md(s) {\n  if (s.length) /`/.test(s);\n"
          "  return `\n  router.get('/internal/debug', list)\n  `;\n}\nexport default router;\n")
    check("a regex literal right after an `if (...)` head is a regex (its backtick opens no template), so a "
          "template's text never becomes a route", _e(T.recognize(rx, "src/routes/md.ts")[1], "ROUTES_TO") == [],
          _e(T.recognize(rx, "src/routes/md.ts")[1], "ROUTES_TO"))
    heads = "while (x) /a`b/.test(y);\nfor (;;) /c/.test(z);\nf(x) / 2 / g;\n"
    check("...the same after `while (...)` and `for (...)` heads (the regex body is blanked); after a call's "
          "`)` a `/` stays a division", T._lex(heads)[0] == "while (x) /   /.test(y);\nfor (;;) / /.test(z);\n"
          "f(x) / 2 / g;\n", T._lex(heads)[0])
    code = "a = i++ / 2; b = c / d;\nx = j-- / 3; y = z / w;\n"
    check("`i++ / 2` and `j-- / 3` are divisions, not regex literals (nothing is blanked)",
          T._lex(code)[0] == code, T._lex(code)[0])
    tip = ("import React from 'react';\n\nexport function Tip() {\n  return <p>Wrap inline code in ` marks.</p>;\n}\n\n"
           "export const sample = `\nexport class OrdersService extends BaseService {}\n`;\n")
    n, e = T.recognize(tip, "docs/src/Tip.tsx")
    check("a lone backtick in JSX text opens no template: a later code-sample template stays text (no "
          ":Class node, no INHERITS)", not e and [x["name"] for x in n] == [], f"{n} {e}")
    tpl = ("import React from 'react';\nimport { Router } from 'express';\nconst router = Router();\n"
           "export const El = () => <p>{`router.get('/x', list)`} and `a`</p>;\n"
           "export const C = () => <code className={`a ${b}`}>plain</code>;\nconst t = `\nrouter.get('/y', list)\n`;\n")
    n, e = T.recognize(tpl, "src/el.jsx")
    check("...while a template inside a JSX expression (`{`...`}`) or in code is still a template (its text adds "
          "nothing)", e == [] and n == [], f"{n} {e}")
    tagged = ("import { Router } from 'express';\nimport { list } from './h';\nconst router = Router();\n"
              "export const El = () => <b>x</b>; export const q = gql `\nrouter.get('/z', list)\n`;\n")
    check("...and a tagged template right after JSX on its line (no closing tag follows the backtick) too",
          T.recognize(tagged, "src/q.tsx")[1] == [], T.recognize(tagged, "src/q.tsx")[1])
    # A template holding HTML after a `>` comparison is code in every file: after `?`, as a call argument, and
    # inside another template's ${ } (lit's html`...`). Read as JSX text, it hid '/real' and let '/fake' through.
    tail = ("router.get('/real', list);\nconst help = `router.get('/fake', list)`;\n"
            "function badge(n) { return n > 9 ? `<b>9+</b>` : `<b>${n}</b>`; }\nclass Users {}\nmodule.exports = router;\n")
    head = "const express = require('express');\nconst router = express.Router();\nfunction list(req, res) {}\n"
    got = {}
    for shape, line in (("ternary", "const label = n > 0 ? `${n} items</span>` : '';\n"),
                        ("argument", "if (a > b) show(1, `<i>x</i>`);\n"),
                        ("lit", "const t = html`<ul>${items.length > 0 ? html`<li>${items[0]}</li>` : ''}</ul>`;\n"),
                        ("comparison", "const c = a < 2 > tag`<i>q</i>`;\n"),
                        ("type arguments", "const g = tag<Props>`<i>q</i>`;\n"),
                        ("component", "const view = items.length > 0 ? <Items /> : `<i>none</i>`;\n"),
                        ("closing tag", "const v = <b>x</b> && `<i>y</i>`;\n")):
        for ext in (".ts", ".js", ".jsx", ".tsx"):
            n, e = T.recognize(head + line + tail, "src/routes/users" + ext)
            got[(shape, ext)] = ([(x.dst_name, x.attrs.get("path")) for x in _e(e, "ROUTES_TO")], [x["name"] for x in n])
    bad = {k: v for k, v in got.items() if v != ([("list", "/real")], ["Users"])}
    check("a template after a comparison (`n > 0 ? `${n} items</span>``), a call argument after `if (a > b)`, lit's "
          "`${x > 0 ? html`<li>..</li>` : ''}`, a tagged template after `a < 2 >` and one with type arguments "
          "(`tag<Props>`...``), the HTML branch of `cond ? <Items /> : `...`` and a template after a closing tag "
          "(`<b>x</b> && `...``: `</` opens no regex literal) are templates in .js / .jsx / .tsx as in .ts: the "
          "route is '/real', the class after `n > 9 ? `<b>9+</b>`` keeps its node, a template's '/fake' is no route",
          not bad, bad)
    jsx = ("import React from 'react';\nexport function Note() {\n  return <p><b>Note</b> wrap code in ` marks.</p>;\n}\n"
           "export const sample = `\nexport class OrdersService extends BaseService {}\n`;\n")
    n, e = T.recognize(jsx, "src/Note.jsx")
    check("...while JSX text after an inline element (`<p><b>Note</b> wrap code in ` marks.</p>`) is still text: the "
          "code sample after it stays a template", not e and [x["name"] for x in n] == [], f"{n} {e}")
    quoted = head + "export const Install = () => <p>Run `npm i` to install</p>;\n" + tail
    got = [([(x.dst_name, x.attrs.get("path")) for x in _e(e, "ROUTES_TO")], [x["name"] for x in n])
           for n, e in (T.recognize(quoted, "src/routes/users" + ext) for ext in (".js", ".jsx", ".tsx"))]
    check("...and two backticks in one line of JSX text (`<p>Run `npm i` to install</p>`) pair up as a template: "
          "nothing after them is hidden, the '/fake' template stays text",
          got == [([("list", "/real")], ["Users"])] * 3, got)
    after = ("router.get('/real', list);\nconst t = `router.get('/fake', list)`;\nclass After { go() {} }\n"
             "module.exports = router;\n")
    got = {}
    for shape, line in (("text ending in a keyword", "const Tip = () => <p>Press return ` to send</p>;\n"),
                        ("inside a ${ opened on an earlier line", "const s = `${\n  render(<p>Press ` here</p>)\n}`;\n"),
                        ("after a tag whose attribute holds `>`",
                         "const P = ({ b }) => <p className={`a ${b > 1 ? 'x' : 'y'}`}>Use ` quotes</p>;\n")):
        for ext in (".js", ".jsx", ".tsx"):
            n, e = T.recognize(head + line + after, "src/routes/tips" + ext)
            got[(shape, ext)] = ([(x.dst_name, x.attrs.get("path")) for x in _e(e, "ROUTES_TO")], [x["name"] for x in n])
    bad = {k: v for k, v in got.items() if v != ([("list", "/real")], ["After"])}
    check("a lone backtick in JSX text is text also when the text ends in a keyword (`<p>Press return ` to send</p>`), "
          "inside a `${` opened on an earlier line, and after a tag whose attribute expression holds a `>` "
          "(`className={`a ${b > 1 ? ..}`}`): the route is '/real', a later template's '/fake' is no route, the class "
          "after it keeps its node", not bad, bad)
    # ---- transpiler output (tsc / Babel / a source-mapped bundle) is a copy of the source: never read
    got = [T.recognize(src, rel) for src, rel in ((TSC_CJS, "lib/base.js"), (TSC_ESM, "lib/repo.mjs"),
                                                 (MAPPED_JS, "out/mapped.js"))]
    check("compiled JavaScript (tsc's CommonJS __esModule marker, tsc helpers `(this && this.__decorate)`, a "
          "`//# sourceMappingURL=` trailer) is skipped: no node, no edge", got == [([], [])] * 3, got)
    plain = "const express = require('express');\nclass Hand {}\nmodule.exports = { Hand };\n"
    check("...a hand-written CommonJS file is read", [x["name"] for x in T.recognize(plain, "src/hand.js")[0]]
          == ["Hand"])
    check("drift lock: the compiled-JavaScript markers are graphify_extract._looks_generated's",
          getattr(T, "_COMPILED_JS_MARKERS", None) == tuple(m.decode() for m in GX._JS_BUNDLE_MARKERS),
          getattr(T, "_COMPILED_JS_MARKERS", None))
    # ---- a class named like a runtime / DOM global binds when the repo declares or imports it
    ticket = ("import { Entity, ManyToOne } from 'typeorm';\nimport { Event } from './event.entity';\n"
              "import { File } from './file.entity';\n\n@Entity()\nexport class Ticket {\n"
              "  @ManyToOne(() => Event, (e) => e.tickets, { onDelete: 'CASCADE' })\n  event: Event;\n\n"
              "  @ManyToOne(() => File)\n  file: File;\n}\n\nexport class Reminder extends Event {}\n")
    got = sorted((x.edge_type, x.src_name, x.dst_name) for x in T.recognize(ticket, "src/ticket.entity.ts")[1])
    check("`Event` / `File` imported from the repo are entities: REFERENCES + CASCADES, and `extends Event` "
          "-> INHERITS", got == [("CASCADES", "Event", "Ticket"), ("INHERITS", "Reminder", "Event"),
                                ("REFERENCES", "Ticket", "Event"), ("REFERENCES", "Ticket", "File")], got)
    glob = ("import { Injectable } from '@nestjs/common';\n\n@Injectable()\nexport class Bus {\n"
            "  constructor(private readonly ev: Event, private readonly doc: Document) {}\n}\n"
            "export class Failure extends Error {}\n\nclass Request {}\nexport class Signed extends Request {}\n")
    got = sorted((x.edge_type, x.dst_name, x.conf_hint) for x in T.recognize(glob, "src/bus.ts")[1])
    check("...but the runtime's own Event / Document / Error (not imported, not declared) never are; a class the "
          "file declares under such a name is (LOCAL)", got == [("INHERITS", "Request", "LOCAL")], got)
    # ---- MikroORM reference wrappers and `new Collection<X>(this)` initializers
    mikro = ("import { Entity, ManyToOne, ManyToMany, OneToMany, Collection, LazyRef, IdentifiedReference, EntityRef } "
             "from '@mikro-orm/core';\nimport { User } from './user.entity';\nimport { Tag } from './tag.entity';\n"
             "import { Book } from './book.entity';\nimport { Team } from './team.entity';\n\n@Entity()\n"
             "export class Article {\n  @ManyToOne() author!: LazyRef<User>;\n"
             "  @ManyToOne() reviewer!: IdentifiedReference<User>;\n  @ManyToOne() team!: EntityRef<Team>;\n"
             "  @ManyToMany({ hidden: true }) tags = new Collection<Tag>(this);\n"
             "  @OneToMany({ mappedBy: 'article' }) books = new Collection<Book, Article>(this);\n}\n")
    got = [(x.edge_type, x.dst_name, x.attrs.get("property")) for x in T.recognize(mikro, "src/article.entity.ts")[1]]
    check("MikroORM: `LazyRef<User>` / `IdentifiedReference<User>` / `EntityRef<Team>` -> REFERENCES, "
          "`tags = new Collection<Tag>(this)` / `new Collection<Book, Article>(this)` -> HAS_MANY",
          got == [("REFERENCES", "User", "author"), ("REFERENCES", "User", "reviewer"), ("REFERENCES", "Team", "team"),
                  ("HAS_MANY", "Tag", "tags"), ("HAS_MANY", "Book", "books")], got)
    # ---- a wrapped class property is a handler node only when it is a handler
    store = ("export class SettingsStore {\n  config = tryParse(() => JSON.parse('{}'));\n"
             "  total = asyncComputed(async () => 1);\n  retries = withDefault(() => 3);\n"
             "  handlerCount = countHandlers((h) => h.length);\n"
             "  getUsers = asyncHandler(async (req, res) => {\n    res.json([]);\n  });\n"
             "  list = catchAsync(async () => []);\n  remove = withAuth(async (ctx) => {});\n"
             "  plain = async (req, res) => res;\n}\n")
    got = [x["name"] for x in T.recognize(store, "src/store.ts")[0]]
    check("a call-wrapped property gets a :Method node only for a known handler wrapper (asyncHandler, catchAsync) "
          "or a handler-shaped function ((req, res), (ctx)); `tryParse(() => ...)`, `asyncComputed(...)`, "
          "`withDefault(...)`, `countHandlers((h) => ...)` hold values",
          got == ["SettingsStore", "SettingsStore.getUsers", "SettingsStore.list", "SettingsStore.remove",
                  "SettingsStore.plain"], got)
    # ---- handlers: a nested helper never shadows an import; an instance's class comes from its import
    nested = ("const express = require('express');\nconst { list } = require('../controllers/users');\n"
              "const router = express.Router();\n\nrouter.get('/users', list);\nrouter.get('/local', local);\n\n"
              "function format(r) {\n  function list(items) {\n    return items;\n  }\n  return list(r);\n}\n\n"
              "function local(req, res) {}\nmodule.exports = router;\n")
    got = [(x.dst_name, x.conf_hint, x.scope) for x in T.recognize(nested, "src/routes/users.js")[1]]
    check("a function nested in another function is not in scope at the route: the imported `list` binds (NAME, "
          "its module), a module-level function stays LOCAL",
          got == [("list", "NAME", "pref:module:src/controllers/users"), ("local", "LOCAL", "")], got)
    inst = ("import { Router } from 'express';\nimport { UsersController } from './users.controller';\n"
            "import { OrdersController as OC } from './orders.controller';\n\nclass Local {\n  h() {}\n}\n"
            "const router = Router();\nconst users = new UsersController();\nconst orders = new OC();\n"
            "const local = new Local();\nconst cache = new Map();\n\nrouter.get('/users', users.list);\n"
            "router.get('/orders', orders.list);\nrouter.get('/local', local.h);\nrouter.get('/cache', cache.get);\n")
    got = [(x.dst_name, x.conf_hint, x.scope) for x in T.recognize(inst, "apps/shop/src/routes.ts")[1]]
    after_cls = ("import { Router } from 'express';\n\nclass Helper {\n  list() {}\n}\n\nexport const api = {\n"
                 "  mount(router: Router) {\n    router.get('/x', this.list);\n  },\n  list() {},\n};\n")
    obj = [(x.dst_name, x.conf_hint) for x in _e(T.recognize(after_cls, "src/api.ts")[1], "ROUTES_TO")]
    check("`this.list` in an object literal's method after a class names no class (`this` is the object): no route, "
          "never `Helper.list`", obj == [], obj)
    check("`x = new X()` handlers: X's import names the module (`users.list` -> UsersController.list in "
          "./users.controller; an alias `new OC()` -> OrdersController.list); a local class stays LOCAL; a Map "
          "instance names no handler",
          got == [("UsersController.list", "NAME", "pref:module:apps/shop/src/users.controller"),
                  ("OrdersController.list", "NAME", "pref:module:apps/shop/src/orders.controller"),
                  ("Local.h", "LOCAL", "")], got)


def _tree(base: Path, name: str, files: dict, extra_nodes=()) -> tuple:
    """(edges after finalize, resolved rows) of the TypeScript entry over a small tree: rows resolve against
    the reader's own ensured nodes plus `extra_nodes` (name, file, label) standing in for the engine's."""
    root = base / "r6" / name
    _write_app(root, files)
    with _env(**dict(_FLAGS, DEEPMAP_RECOGNIZERS="typescript")), contextlib.redirect_stderr(io.StringIO()):
        supp, edges = LR.run(root)
    nodes = list(supp) + [{"key": f"{sf}::{nm}", "label": lb, "name": nm, "source_file": sf}
                          for nm, sf, lb in extra_nodes]
    by = {n["key"]: (n["name"], n["source_file"]) for n in nodes}
    keys = [n["key"] for n in nodes]
    rows = sorted((r[0], by[r[1]], by[r[2]], r[3]) for r in ER.resolve_typed_edges(edges, ER.Resolver(nodes), keys))
    return edges, rows


def _route_rows(edges, nodes) -> list:
    """(dst (name, file, line), confidence, path, routes) of each ROUTES_TO row `edges` resolve to over `nodes`
    ((name, file, label, line) standing in for the engine's: which lines a version node-ifies decides the row)."""
    ns = [{"key": f"{sf}::{nm}:{ln}", "label": lb, "name": nm, "source_file": sf, "start_line": ln}
          for nm, sf, lb, ln in nodes]
    by = {n["key"]: (n["name"], n["source_file"], n["start_line"]) for n in ns}
    return sorted((by[r[2]], r[3], (r[4] or {}).get("path"), tuple((r[4] or {}).get("routes") or ()))
                  for r in ER.resolve_typed_edges(edges, ER.Resolver(ns), [n["key"] for n in ns]) if r[0] == "ROUTES_TO")


_NEST_INJ = ("import {{ Injectable }} from '@nestjs/common';\nimport {{ {cls} }} from '{spec}';\n\n@Injectable()\n"
             "export class {name} {{\n  constructor(private readonly dep: {cls}) {{}}\n}}\n")


def gate_import_resolution(base: Path) -> None:
    print("\n-- R6: imports resolved against the tree: tsconfig paths / baseUrl, workspace packages, barrels, defaults --")
    if T is None:
        check("build/typescript_edges.py exists (the recognizer)", False, "ImportError")
        return
    svc = "import { Injectable } from '@nestjs/common';\n\n@Injectable()\nexport class LoggerService {}\n"
    lib = {"tsconfig.json": ('{\n  // Nest CLI monorepo\n  "compilerOptions": {\n    "baseUrl": "./",\n    "paths": {\n'
                             '      "@app/common": ["libs/common/src"],\n      "@app/common/*": ["libs/common/src/*"],\n'
                             '    },\n  },\n}\n'),
           "libs/common/src/index.ts": "export * from './logger.service';\n",
           "libs/common/src/logger.service.ts": svc,
           "apps/admin/src/common.ts": "export class LoggerService {}\n",
           "apps/api/src/users.service.ts": _NEST_INJ.format(cls="LoggerService", spec="@app/common", name="UsersService"),
           "apps/api/src/orders.service.ts": _NEST_INJ.format(cls="LoggerService", spec="@app/common/logger.service",
                                                             name="OrdersService")}
    _ed, rows = _tree(base, "tsc_paths", lib)
    check("tsconfig `paths` (JSON with comments and trailing commas): `@app/common` -> libs/common/src (its index "
          "re-exports the class) binds that LoggerService, INFERRED — never the admin app's common.ts",
          rows == [("DEPENDS_ON", ("OrdersService", "apps/api/src/orders.service.ts"),
                    ("LoggerService", "libs/common/src/logger.service.ts"), "INFERRED"),
                   ("DEPENDS_ON", ("UsersService", "apps/api/src/users.service.ts"),
                    ("LoggerService", "libs/common/src/logger.service.ts"), "INFERRED")], rows)
    nx = {"tsconfig.base.json": '{ "compilerOptions": { "baseUrl": ".", "paths": { "@acme/data": ["libs/data/src/index.ts"] } } }\n',
          "apps/shop/tsconfig.json": '{ "extends": "../../tsconfig.base.json", "compilerOptions": { "strict": true } }\n',
          "libs/data/src/index.ts": "export { Repo } from './lib/repo';\n",
          "libs/data/src/lib/repo.ts": "export class Repo {}\n",
          "apps/legacy/src/repo.ts": "export class Repo {}\n",
          "apps/shop/src/cart.service.ts": _NEST_INJ.format(cls="Repo", spec="@acme/data", name="CartService")}
    _ed, rows = _tree(base, "tsc_extends", nx)
    check("an app tsconfig that `extends` the root tsconfig.base.json inherits its `paths`; a named re-export "
          "(`export { Repo } from './lib/repo'`) is followed",
          rows == [("DEPENDS_ON", ("CartService", "apps/shop/src/cart.service.ts"), ("Repo", "libs/data/src/lib/repo.ts"),
                    "INFERRED")], rows)
    base_url = {"tsconfig.json": '{ "compilerOptions": { "baseUrl": "./src" } }\n',
                "src/common/guards/roles.guard.ts": "export class RolesGuard {}\n",
                "src/users/users.service.ts": "export class UsersService {}\n",
                "src/shared/base.controller.ts": "export class BaseController {}\n",
                "legacy/users.service.ts": "export class UsersService {}\n",
                "src/users/users.controller.ts": (
                    "import { Controller, Get, UseGuards } from '@nestjs/common';\n"
                    "import { RolesGuard } from 'common/guards/roles.guard';\nimport { UsersService } from 'users/users.service';\n"
                    "import { BaseController } from 'shared/base.controller';\nimport { Logger } from 'winston-pkg';\n\n"
                    "@Controller('users')\n@UseGuards(RolesGuard)\nexport class UsersController extends BaseController {\n"
                    "  constructor(private readonly users: UsersService, private readonly log: Logger) {\n    super();\n  }\n}\n"),
                "src/logger.ts": "export class Logger {}\n"}
    base_url.update({"src/domain/user.entity.ts": "export class User {}\n",
                     "src/events/bus.ts": "export class Bus {}\n",
                     "src/users/user.service.ts": (
                         "import { Injectable } from '@nestjs/common';\nimport { User } from 'domain/user.entity';\n"
                         "import { Bus } from 'events/bus';\nimport { EventEmitter } from 'events';\n\n@Injectable()\n"
                         "export class UserService extends EventEmitter {\n"
                         "  constructor(private readonly user: User, private readonly bus: Bus) {\n    super();\n  }\n}\n"),
                     "legacy/event-emitter.ts": "export class EventEmitter {}\n"})
    _ed, rows = _tree(base, "base_url", base_url)
    us = [r for r in rows if r[1][0] == "UserService"]
    check("TypeScript resolves a bare specifier through `baseUrl` before node_modules: `domain/user.entity` and "
          "`events/bus` (directories named like node builtins) are repo modules; `events` alone, with no "
          "src/events.ts or src/events/index.ts, is the builtin (no INHERITS to the repo's EventEmitter)",
          us == [("DEPENDS_ON", ("UserService", "src/users/user.service.ts"), ("Bus", "src/events/bus.ts"), "INFERRED"),
                 ("DEPENDS_ON", ("UserService", "src/users/user.service.ts"), ("User", "src/domain/user.entity.ts"),
                  "INFERRED")], us)
    rows = [r for r in rows if r[1][0] != "UserService"]
    u = ("UsersController", "src/users/users.controller.ts")
    check("tsconfig `baseUrl`: `common/guards/roles.guard`, `users/users.service`, `shared/base.controller` are repo "
          "modules (HOOKS / DEPENDS_ON / INHERITS bind exactly); `winston-pkg` names no module of the tree, so its "
          "Logger never binds the repo's",
          rows == [("DEPENDS_ON", u, ("UsersService", "src/users/users.service.ts"), "INFERRED"),
                   ("HOOKS", u, ("RolesGuard", "src/common/guards/roles.guard.ts"), "INFERRED"),
                   ("INHERITS", u, ("BaseController", "src/shared/base.controller.ts"), "INFERRED")], rows)
    dflt = {"server/entities/user.entity.ts": "import { Entity } from 'typeorm';\n\n@Entity()\nexport default class UserEntity {}\n",
            "server/entities/photo.entity.ts": (
                "import { Entity, ManyToOne } from 'typeorm';\nimport User from './user.entity';\n\n@Entity()\n"
                "export class Photo {\n  @ManyToOne(() => User, { onDelete: 'CASCADE' })\n  user: User;\n}\n"),
            "client/models/user.ts": "export class User {}\n",
            "apps/api/src/shared/index.ts": "export { BaseEntity } from 'typeorm';\nexport { Audited } from './audited';\n",
            "apps/api/src/shared/audited.ts": "export class Audited {}\n",
            "apps/api/src/users/member.entity.ts": ("import { BaseEntity, Audited } from '../shared';\n"
                                                    "export class Member extends BaseEntity {}\nexport class Admin extends Audited {}\n"),
            "apps/api/src/users/direct.entity.ts": ("import { BaseEntity } from 'typeorm';\nimport { Controller } from 'tsoa';\n"
                                                    "export class Direct extends BaseEntity {}\n"
                                                    "export class Api extends Controller {}\n"),
            "libs/legacy/src/controller.ts": "export class Controller {}\n",
            "libs/legacy/src/base-entity.ts": "export class BaseEntity {}\n",
            "apps/api/src/types.ts": "export type Request = { id: string };\n",
            "apps/api/src/http/request.ts": "export class Request {}\n",
            "apps/api/src/users/users.service.ts": _NEST_INJ.format(cls="Request", spec="../types", name="ReqService")}
    _ed, rows = _tree(base, "defaults", dflt)
    check("a default import under another name (`import User from './user.entity'`) binds the module's default "
          "export UserEntity (REFERENCES, and CASCADES UserEntity -> Photo) — never the client's User; a barrel's "
          "`export { BaseEntity } from 'typeorm'` and a direct `typeorm` / `tsoa` import are external (no INHERITS "
          "to the repo's BaseEntity / Controller) while a repo re-export binds; a type alias (`export type "
          "Request`) binds in its own module or nowhere (never the other file's Request)",
          rows == [("CASCADES", ("UserEntity", "server/entities/user.entity.ts"), ("Photo", "server/entities/photo.entity.ts"),
                    "INFERRED"),
                   ("INHERITS", ("Admin", "apps/api/src/users/member.entity.ts"), ("Audited", "apps/api/src/shared/audited.ts"),
                    "INFERRED"),
                   ("REFERENCES", ("Photo", "server/entities/photo.entity.ts"), ("UserEntity", "server/entities/user.entity.ts"),
                    "INFERRED")], rows)
    pkgs = {"client/src/models/User.ts": "import { Model } from '@vuex-orm/core';\nexport default class User extends Model {}\n",
            "server/src/db/model.ts": "export class Model {}\n",
            "packages/models/package.json": '{ "name": "@acme/models", "main": "src/index.ts" }\n',
            "packages/models/src/index.ts": "export * from './model';\n",
            "packages/models/src/model.ts": "export class Model {}\n",
            "apps/web/src/post.ts": "import { Model } from '@acme/models';\nexport class Post extends Model {}\n"}
    ed, rows = _tree(base, "workspace", pkgs)
    check("an npm scope the tree has no package for (`@vuex-orm/core`) is external: no INHERITS to the repo's "
          "Model; a workspace package (`@acme/models`, its package.json `main`, an `export *` index) binds its own",
          rows == [("INHERITS", ("Post", "apps/web/src/post.ts"), ("Model", "packages/models/src/model.ts"), "INFERRED")],
          rows)
    check("...finalize itself drops the external one: no edge leaves it with an unplaced specifier (spec:)",
          [x.src_name for x in ed if "spec:" in x.scope] == [] and [x.src_name for x in ed] == ["Post"],
          [(x.src_name, x.scope) for x in ed])
    koa = {"src/controller/index.ts": "export { default as general } from './general';\nexport { default as user } from './user';\n",
           "src/controller/general.ts": ("import { Context } from 'koa';\n\nexport default class GeneralController {\n"
                                         "  public static async helloWorld(ctx: Context): Promise<void> {\n    ctx.body = 'hi';\n  }\n}\n"),
           "src/controller/user.ts": ("import { Context } from 'koa';\n\nexport default class UserController {\n"
                                      "  public static async getUsers(ctx: Context): Promise<void> {\n    ctx.body = [];\n  }\n}\n"),
           "src/controllers/index.ts": "export * from './orders';\n",
           "src/controllers/orders.ts": "export async function list(ctx) {}\n",
           "src/routes.ts": ("import Router from '@koa/router';\nimport { general, user } from './controller';\n"
                             "import * as ctrl from './controllers';\n\nconst router = new Router();\n"
                             "router.get('/', general.helloWorld);\nrouter.get('/users', user.getUsers);\n"
                             "router.get('/orders', ctrl.list);\nexport { router };\n")}
    ed, rows = _tree(base, "barrel_objects", koa, extra_nodes=(
        ("GeneralController.helloWorld", "src/controller/general.ts", "Method"),
        ("UserController.getUsers", "src/controller/user.ts", "Method"), ("list", "src/controllers/orders.ts", "Function"),
        ("routes", "src/routes.ts", "Module")))
    got = [(x.dst_name, x.scope) for x in _e(ed, "ROUTES_TO")]
    check("controller objects imported through a barrel (`export { default as user } from './user'`, a namespace "
          "`import * as ctrl` over `export *`) resolve inside the module that defines them (a default-exported "
          "class's member is that class's method; a namespace's member is placed like a named import: exactly, at "
          "its line)",
          got == [("GeneralController.helloWorld", "exact:module:src/controller/general"),
                  ("UserController.getUsers", "exact:module:src/controller/user"),
                  ("list", "L0,1|exact:module:src/controllers/orders")]
          and [(r[2][0], r[3]) for r in rows] == [("GeneralController.helloWorld", "INFERRED"),
                                                  ("UserController.getUsers", "INFERRED"), ("list", "INFERRED")],
          f"{got} {rows}")
    inst = {"src/controllers/index.ts": ("export { default as users } from './users.controller';\n"
                                         "export { default as orders } from './orders.controller';\n"
                                         "export { default as audits } from './audits.controller';\n"),
            "src/controllers/users.controller.ts": ("class UsersController {\n  list(req, res) {}\n}\n"
                                                    "export default new UsersController();\n"),
            "src/controllers/orders.controller.ts": "async function list(req, res) {}\nexport default { list };\n",
            "src/controllers/audits.controller.js": ("class AuditsController {\n  list(req, res) {}\n}\n"
                                                     "module.exports = new AuditsController();\n"),
            "src/legacy/list.ts": "export function list(req, res) {}\n",
            "src/handlers/index.ts": "export { default as purge } from './purge';\n",
            "src/handlers/purge.ts": ("function purge(items) {\n  return items;\n}\n"
                                      "export default async function (req, res) {\n  res.json(purge([]));\n}\n"),
            "src/routes.ts": ("import { Router } from 'express';\nimport { users, orders, audits } from './controllers';\n"
                              "import * as handlers from './handlers';\n"
                              "const router = Router();\nrouter.get('/users', users.list);\nrouter.get('/orders', orders.list);\n"
                              "router.get('/audits', audits.list);\nrouter.delete('/all', handlers.purge);\n"
                              "export default router;\n")}
    ed, rows = _tree(base, "barrel_instances", inst, extra_nodes=(
        ("UsersController.list", "src/controllers/users.controller.ts", "Method"),
        ("list", "src/controllers/orders.controller.ts", "Function"),
        ("AuditsController.list", "src/controllers/audits.controller.js", "Method"),
        ("list", "src/legacy/list.ts", "Function"), ("purge", "src/handlers/purge.ts", "Function"),
        ("routes", "src/routes.ts", "Module")))
    check("a controller object behind a barrel whose module default-exports an instance (`export default new "
          "UsersController()`, `module.exports = new AuditsController()`) or an object literal (`export default "
          "{ list }`) resolves inside that module, as a direct import of the module does — never dropped, never "
          "another `list`; a namespace member that is an anonymous default FUNCTION (`handlers.purge`) is no symbol: "
          "no edge, never the module's helper named `purge`",
          [r[2] for r in rows] == [("AuditsController.list", "src/controllers/audits.controller.js"),
                                   ("UsersController.list", "src/controllers/users.controller.ts"),
                                   ("list", "src/controllers/orders.controller.ts")]
          and {r[3] for r in rows} == {"INFERRED"}, f"{rows} {[(x.dst_name, x.scope) for x in ed]}")
    forms = {"src/users.ts": "export function create(req, res) {}\nexport function list(req, res) {}\n",
             "src/routes.ts": ("import { Router } from 'express';\nimport { create as createUser } from './users';\n"
                               "import * as users from './users';\nimport { list as listUsers } from './users';\n"
                               "const router = Router();\nfunction create(req, res) {}\n"
                               "router.post('/users', createUser);\nrouter.post('/local', create);\n"
                               "router.get('/users', listUsers);\nrouter.get('/u2', users.list);\nexport default router;\n")}
    fnodes = (("create", "src/users.ts", "Function"), ("list", "src/users.ts", "Function"),
              ("create", "src/routes.ts", "Function"), ("routes", "src/routes.ts", "Module"))
    ed, _rows = _tree(base, "import_forms", forms, extra_nodes=fnodes)
    nodes = [{"key": f"{sf}::{nm}", "label": lb, "name": nm, "source_file": sf} for nm, sf, lb in fnodes]
    got = sorted((r[2], r[3], r[4].get("path"), tuple(r[4].get("routes") or ())) for r in
                 ER.resolve_typed_edges(ed, ER.Resolver(nodes), [n["key"] for n in nodes]))
    check("an imported handler binds the module its import names even when the importing file declares a function of "
          "the imported name (`create as createUser` beside a local `create`: two rows, each with its own path); one "
          "handler reached through two import forms (`list as listUsers`, `users.list`) is one edge keeping both routes",
          got == [("src/routes.ts::create", "EXTRACTED", "/local", ()),
                  ("src/users.ts::create", "INFERRED", "/users", ()),
                  ("src/users.ts::list", "INFERRED", "/users", ("GET /u2", "GET /users"))], got)
    zod = {"src/schemas.ts": ("export interface ZodStringFormat<F extends string = string>\n  extends Base<F> {}\n"
                              "export const ZodStringFormat: core.$constructor<ZodStringFormat> = core.$constructor(\n"
                              "  'ZodStringFormat',\n  (inst, def) => {},\n);\nexport interface ZodISO extends ZodStringFormat {\n"
                              "  _zod: number;\n}\n"
                              "export const ZodISO: core.$constructor<ZodISO> = core.$constructor('ZodISO', (i, d) => {});\n"),
           "src/iso.ts": ("import { ZodStringFormat } from './schemas';\n\nexport interface ZodMiniISO extends ZodStringFormat {}\n"
                          "export const ZodMiniISO: core.$constructor<ZodMiniISO> = core.$constructor('ZodMiniISO', (i, d) => {});\n"),
           "src/dual.ts": ("export const Dual: core.$constructor<Dual> = core.$constructor('Dual', (i, d) => {});\n"
                           "export interface Dual {\n  x: number;\n}\n"),
           "src/svc.ts": ("import { Injectable } from '@nestjs/common';\nimport { ZodStringFormat } from './schemas';\n"
                          "import { Dual } from './dual';\n\n@Injectable()\nexport class Svc {\n"
                          "  constructor(private readonly f: ZodStringFormat, private readonly d: Dual) {}\n}\n")}
    root = base / "r6" / "zod"
    _write_app(root, zod)
    with _env(**dict(_FLAGS, DEEPMAP_RECOGNIZERS="typescript")), contextlib.redirect_stderr(io.StringIO()):
        _supp, edges = LR.run(root)
    # the engine's nodes: an interface AND its `const` companion are two :Class nodes of one name (zod v4)
    znodes = [{"key": f"{sf}::{nm}:{ln}", "label": "Class", "name": nm, "source_file": sf, "start_line": ln}
              for nm, sf, ln in (("ZodStringFormat", "src/schemas.ts", 1), ("ZodStringFormat", "src/schemas.ts", 3),
                                 ("ZodISO", "src/schemas.ts", 7), ("ZodISO", "src/schemas.ts", 10),
                                 ("ZodMiniISO", "src/iso.ts", 3), ("ZodMiniISO", "src/iso.ts", 4),
                                 ("Dual", "src/dual.ts", 1), ("Dual", "src/dual.ts", 2), ("Svc", "src/svc.ts", 6))]
    zrows = sorted((r[0], r[1].split("::")[1], r[2].split("::")[1], r[3]) for r in
                   ER.resolve_typed_edges(edges, ER.Resolver(znodes), [n["key"] for n in znodes]))
    check("`interface X extends Y` where the engine also node-ifies X's and Y's `const` companions: the edge binds "
          "the interfaces (the declaration lines), one row each — not 2 x 2 AMBIGUOUS rows; an injected type binds "
          "the interface too (declared after its const or before it)",
          zrows == [("DEPENDS_ON", "Svc:6", "Dual:2", "INFERRED"), ("DEPENDS_ON", "Svc:6", "ZodStringFormat:1", "INFERRED"),
                    ("INHERITS", "ZodISO:7", "ZodStringFormat:1", "EXTRACTED"),
                    ("INHERITS", "ZodMiniISO:3", "ZodStringFormat:1", "INFERRED")], zrows)
    # a barrel lattice: index -> 10 barrels -> each of 10 more barrels -> each of 10 leaves (none exports Ghost)
    lattice = {"src/index.ts": "".join(f"export * from './a{i}';\n" for i in range(10)),
               "src/user.service.ts": _NEST_INJ.format(cls="Ghost", spec="./index", name="UserService")}
    for i in range(10):
        lattice[f"src/a{i}.ts"] = "".join(f"export * from './b{j}';\n" for j in range(10))
        lattice[f"src/b{i}.ts"] = "".join(f"export * from './c{j}';\n" for j in range(10))
        lattice[f"src/c{i}.ts"] = f"export class Leaf{i} {{}}\n"
    calls = [0]
    real = T._Modules.lookup

    def counted(self, *a, **kw):
        calls[0] += 1
        return real(self, *a, **kw)
    T._Modules.lookup = counted
    try:
        ed, _rows = _tree(base, "lattice", lattice)
    finally:
        T._Modules.lookup = real
    check("a name looked up through a lattice of `export *` barrels (10 x 10 x 10) follows each of its 210 "
          "re-export edges once (memoized: 211 lookups, not one per path: 1111); a name no module exports is dropped",
          calls[0] <= 211 and _e(ed, "DEPENDS_ON") == [], (calls[0], _e(ed, "DEPENDS_ON")))
    sub = {"package.json": ('{ "name": "svc", "type": "module", "imports": { "#src/*": "./src/*", '
                            '"#src/handlers/*": "./src/http/handlers/*", "#logger": "pino", '
                            '"#db": { "types": "./src/db/index.ts", "default": "./dist/db/index.js" } } }\n'),
           "src/log/app-logger.ts": "import { Logger } from '#logger';\nexport class AppLogger extends Logger {}\n",
           "legacy/logger.ts": "export class Logger {}\n",
           "src/http/handlers/ping.ts": "export async function ping(req, reply) {}\n",
           "src/handlers/ping.ts": "export async function ping(req, reply) {}\n",
           "src/controllers/users.ts": "export async function list(req, reply) {}\nexport async function show(req, reply) {}\n",
           "other/controllers/users.ts": "export async function list(req, reply) {}\nexport async function show(req, reply) {}\n",
           "src/shared/base.ts": "export class BaseRepo {}\n", "legacy/base.ts": "export class BaseRepo {}\n",
           "src/db/index.ts": "export { Store } from './store';\n", "src/db/store.ts": "export class Store {}\n",
           "legacy/store.ts": "export class Store {}\n",
           "src/repos/users.repo.ts": ("import { BaseRepo } from '#src/shared/base.ts';\nimport { Store } from '#db';\n"
                                       "export class UsersRepo extends BaseRepo {}\nexport class Cache extends Store {}\n"),
           "src/routes/users.ts": ("import express from 'express';\nimport * as users from '#src/controllers/users.ts';\n"
                                   "import { show } from '#src/controllers/users.js';\n"
                                   "import { ping } from '#src/handlers/ping.js';\nconst router = express.Router();\n"
                                   "router.get('/', users.list);\nrouter.get('/:id', show);\nrouter.get('/ping', ping);\n"
                                   "export default router;\n"),
           "src/app.ts": ("import express from 'express';\nimport usersRouter from '#src/routes/users.js';\n"
                          "const app = express();\napp.use('/api/users', usersRouter);\n")}
    ed, rows = _tree(base, "subpath_imports", sub, extra_nodes=(
        ("list", "src/controllers/users.ts", "Function"), ("show", "src/controllers/users.ts", "Function"),
        ("list", "other/controllers/users.ts", "Function"), ("show", "other/controllers/users.ts", "Function"),
        ("ping", "src/http/handlers/ping.ts", "Function"), ("ping", "src/handlers/ping.ts", "Function"),
        ("users", "src/routes/users.ts", "Module")))
    check("Node.js subpath imports (package.json `imports`: `#src/*`, the longer `#src/handlers/*` pattern winning for "
          "its names, a conditions object for `#db`) are placed in the tree: a module-object handler (`users.list`) "
          "and a named one (`show`, a NodeNext `.js` name) bind src/controllers/users.ts, `ping` binds "
          "src/http/handlers/ping.ts; `extends BaseRepo` / `extends Store` (through `#db`'s index) bind src/ — "
          "never other/, legacy/ or src/handlers/; `#logger` maps to a package (pino), so `extends Logger` binds "
          "nothing; the router mounted through `#src/routes/users.js` composes /api/users",
          rows == [("INHERITS", ("Cache", "src/repos/users.repo.ts"), ("Store", "src/db/store.ts"), "INFERRED"),
                   ("INHERITS", ("UsersRepo", "src/repos/users.repo.ts"), ("BaseRepo", "src/shared/base.ts"), "INFERRED"),
                   ("ROUTES_TO", ("users", "src/routes/users.ts"), ("list", "src/controllers/users.ts"), "INFERRED"),
                   ("ROUTES_TO", ("users", "src/routes/users.ts"), ("ping", "src/http/handlers/ping.ts"), "INFERRED"),
                   ("ROUTES_TO", ("users", "src/routes/users.ts"), ("show", "src/controllers/users.ts"), "INFERRED")]
          and sorted(x.attrs.get("path") for x in _e(ed, "ROUTES_TO")) == ["/api/users", "/api/users/:id",
                                                                            "/api/users/ping"],
          f"{rows} {[(x.dst_name, x.attrs.get('path'), x.scope) for x in ed]}")
    guess = {"libs/common/src/index.ts": "export * from './logger.service';\n",
             "libs/common/src/logger.service.ts": "export class LoggerService {}\n",
             "apps/admin/src/common.ts": "export class LoggerService {}\n",
             "apps/api/src/users.service.ts": _NEST_INJ.format(cls="LoggerService", spec="@/common", name="UsersService")}
    _ed, rows = _tree(base, "alias_guess", guess)
    check("an alias no tsconfig maps (`@/common`) is a guess: it never narrows two same-named classes to the one "
          "whose path happens to end with it (both AMBIGUOUS, never one INFERRED)",
          [(r[2][1], r[3]) for r in rows] == [("apps/admin/src/common.ts", "AMBIGUOUS"),
                                              ("libs/common/src/logger.service.ts", "AMBIGUOUS")], rows)
    # ---- a CommonJS property export (`exports.list = <expr>`) is that export's own definition: an import of it
    # binds the engine's node there or nothing (graphify 0.8.39 node-ifies no `exports.x = function`, and no
    # version a wrapped one) — never the importing file's own `list`, never another module's
    cjs_head = "const express = require('express');\nconst router = express.Router();\n"
    cjs_routes = (cjs_head + "const { list: listUsers } = require('./controllers/users');\nfunction list(req, res) {}\n"
                  "router.get('/users', listUsers);\nrouter.get('/local', list);\nmodule.exports = router;\n")
    lone = (cjs_head + "const { list: listUsers } = require('./controllers/users');\nrouter.get('/users', listUsers);\n"
            "module.exports = router;\n")
    orders = "function list(req, res) {}\nmodule.exports = { list };\n"
    wrapped = "const catchAsync = (fn) => fn;\nexports.list = catchAsync(async (req, res) => { res.send([]); });\n"
    plain = "exports.list = async function (req, res) {};\n"
    rmod, rlist, olist = (("routes", "src/routes.js", "Module", 1), ("list", "src/routes.js", "Function", 4),
                          ("list", "src/controllers/orders.js", "Function", 1))
    got = {}
    for name, users_src, routes_src, nodes in (
            ("wrapped", wrapped, cjs_routes, [rmod, rlist, olist]),
            ("plain, 0.8.39", plain, cjs_routes, [rmod, rlist, olist]),
            ("plain, 0.8.51", plain, cjs_routes, [rmod, rlist, olist, ("list", "src/controllers/users.js", "Function", 1)]),
            ("no local list", wrapped, lone, [rmod, olist])):
        ed, _rows = _tree(base, "cjs_" + name.split(",")[0].replace(" ", "_"), {
            "src/controllers/users.js": users_src, "src/controllers/orders.js": orders, "src/routes.js": routes_src})
        got[name] = _route_rows(ed, nodes)
    # a module the reader cannot read (compiled output) keeps the import "pref:": its global fallback still never
    # offers the importing file's own `list`
    compiled = ('"use strict";\nObject.defineProperty(exports, "__esModule", { value: true });\nexports.list = void 0;\n'
                "exports.list = (0, wrap_1.default)(async (req, res) => { });\n")
    ed, _rows = _tree(base, "cjs_compiled", {"src/controllers/users.js": compiled, "src/routes.js": cjs_routes})
    got["unreadable module"] = _route_rows(ed, [rmod, rlist])
    local = (("list", "src/routes.js", 4), "EXTRACTED", "/local", ())
    check("an import of a CommonJS property export (`const { list: listUsers } = require(...)` of `exports.list = "
          "catchAsync(...)` / `= async function`) binds the engine's node at that `exports.list` or nothing — never "
          "the importing file's own `list` (whose '/local' row stays) nor another module's `list`; when the module "
          "cannot be read, the global fallback still never offers the importing file's own `list`",
          got == {"wrapped": [local], "plain, 0.8.39": [local],
                  "plain, 0.8.51": [(("list", "src/controllers/users.js", 1), "INFERRED", "/users", ()), local],
                  "no local list": [], "unreadable module": [local]}, got)
    # madhums/node-express-mongoose-demo: `exports.login = function` renders the form; a private `function login`
    # below it (exported only as `exports.session = login`) is another handler. 0.8.39 node-ifies only the
    # private one; 0.8.51 one merged `login` node at the first definition.
    ctrl = ("exports.login = function (req, res) {\n  res.render('users/login');\n};\n"
            "exports.session = login;\nfunction login(req, res) {\n  res.redirect('/');\n}\n")
    madhums = {"app/controllers/users.js": ctrl,
               "config/routes.js": ("const users = require('../app/controllers/users');\nmodule.exports = function (app) {\n"
                                    "  app.get('/login', users.login);\n  app.post('/users/session', users.session);\n};\n"),
               "config/routes2.mjs": ("import * as users from '../app/controllers/users.js';\nexport default function (app) {\n"
                                      "  app.get('/login', users.login);\n  app.post('/users/session', users.session);\n}\n")}
    ed, _rows = _tree(base, "madhums", madhums)
    mods = [("routes", "config/routes.js", "Module", 1), ("routes2", "config/routes2.mjs", "Module", 1)]
    got = {ver: [r for r in _route_rows(ed, mods + [("login", "app/controllers/users.js", "Function", line)])]
           for ver, line in (("0.8.39", 5), ("0.8.51", 1))}
    check("a member of a CommonJS module object (`users.login` through require() or `import * as`) binds the "
          "definition the module exports under that name: GET /login -> `exports.login = function` (no node on "
          "0.8.39: no row, never the private `function login`), POST /users/session -> `function login` (0.8.39) — a "
          "node at another definition's line is never bound",
          got == {"0.8.39": [(("login", "app/controllers/users.js", 5), "INFERRED", "/users/session", ())] * 2,
                  "0.8.51": [(("login", "app/controllers/users.js", 1), "INFERRED", "/login", ())] * 2}, got)
    objs = {"app/obj.js": ("function login(req, res) {\n  res.redirect('/');\n}\nmodule.exports = {\n  login(req, res) {\n"
                           "    res.render('login');\n  },\n  list: async (req, res) => {},\n};\n"),
            "config/obj_routes.js": ("const obj = require('../app/obj');\nmodule.exports = function (app) {\n"
                                     "  app.get('/login', obj.login);\n  app.get('/list', obj.list);\n};\n")}
    ed, _rows = _tree(base, "cjs_object", objs)
    omod = ("obj_routes", "config/obj_routes.js", "Module", 1)
    got = {nodes: _route_rows(ed, [omod] + [(nm, "app/obj.js", "Function", ln) for nm, ln in lines])
           for nodes, lines in (("helper only", (("login", 1),)), ("object members", (("login", 1), ("login", 5),
                                                                                     ("list", 8))))}
    check("a `module.exports = { login(req, res) {..}, list: async (..) => {} }` member is defined right there: its "
          "route binds a node at that line or nothing — never the module's private `function login` above it",
          got == {"helper only": [],
                  "object members": [(("list", "app/obj.js", 8), "INFERRED", "/list", ()),
                                     (("login", "app/obj.js", 5), "INFERRED", "/login", ())]}, got)
    chain = {"src/controllers/index.ts": "export * from './group';\n",
             "src/controllers/group.ts": ("export { default as users } from './users.controller';\n"
                                          "export { default as orders } from './orders.controller';\n"),
             "src/controllers/users.controller.ts": ("class UsersController {\n  list(ctx) {}\n}\n"
                                                     "export default new UsersController();\n"),
             "src/controllers/orders.controller.ts": "export function list(ctx) {}\nexport default { list };\n",
             "src/admin/users/index.ts": "export { default } from './users.controller';\n",
             "src/admin/users/users.controller.ts": ("class AdminUsers {\n  list(ctx) {}\n}\n"
                                                     "export default new AdminUsers();\n"),
             "src/admin/orders/index.ts": "export { default } from './orders.controller';\n",
             "src/admin/orders/orders.controller.ts": "function list(ctx) {}\nexport default { list };\n",
             "src/legacy.ts": "export function list(ctx) {}\n",
             "src/routes.ts": ("import Router from '@koa/router';\nimport { users, orders } from './controllers';\n"
                               "import adminUsers from './admin/users';\nimport adminOrders from './admin/orders';\n"
                               "import { list } from './controllers/orders.controller';\n"
                               "const router = new Router();\nrouter.get('/users', users.list);\n"
                               "router.get('/orders', orders.list);\nrouter.get('/admin/users', adminUsers.list);\n"
                               "router.get('/admin/orders', adminOrders.list);\nrouter.get('/o2', list);\nexport default router;\n")}
    ed, _rows = _tree(base, "barrel_chain", chain)
    got = _route_rows(ed, [("routes", "src/routes.ts", "Module", 1),
                           ("UsersController.list", "src/controllers/users.controller.ts", "Method", 2),
                           ("list", "src/controllers/orders.controller.ts", "Function", 1),
                           ("AdminUsers.list", "src/admin/users/users.controller.ts", "Method", 2),
                           ("list", "src/admin/orders/orders.controller.ts", "Function", 1),
                           ("list", "src/legacy.ts", "Function", 1)])
    check("controller objects behind an `export *` chain (index -> group's `export { default as users } from`) and "
          "behind a folder index's `export { default } from` reached by a default import resolve inside the module "
          "that holds the default (an instance or an object literal) — never src/legacy.ts's `list`; a member of a "
          "re-exported object literal is its definition, so it folds with a named import of that handler",
          [(r[0][:2], r[1], r[2], r[3]) for r in got] == [
              (("AdminUsers.list", "src/admin/users/users.controller.ts"), "INFERRED", "/admin/users", ()),
              (("UsersController.list", "src/controllers/users.controller.ts"), "INFERRED", "/users", ()),
              (("list", "src/admin/orders/orders.controller.ts"), "INFERRED", "/admin/orders", ()),
              (("list", "src/controllers/orders.controller.ts"), "INFERRED", "/orders", ("GET /o2", "GET /orders"))],
          got)
    both = {"src/users.ts": "export function list(req, res) {}\nexport default { list };\n",
            "src/routes.ts": ("import { Router } from 'express';\nimport users from './users';\nimport { list } from './users';\n"
                              "const router = Router();\nrouter.get('/users', list);\nrouter.get('/u2', users.list);\n"
                              "export default router;\n"),
            "src/show.js": "function show(req, res) {}\nmodule.exports = { show };\n",
            "src/routes2.js": (cjs_head + "const users = require('./show');\nconst { show } = require('./show');\n"
                               "router.get('/a/:id', show);\nrouter.get('/b/:id', users.show);\nmodule.exports = router;\n")}
    ed, _rows = _tree(base, "import_forms_objects", both)
    got = _route_rows(ed, [("routes", "src/routes.ts", "Module", 1), ("routes2", "src/routes2.js", "Module", 1),
                           ("list", "src/users.ts", "Function", 1), ("show", "src/show.js", "Function", 1)])
    check("one handler reached through a named import and a member of a default-import / require() module object "
          "(`export default { list }`, `module.exports = { show }`) is one edge keeping both routes",
          got == [(("list", "src/users.ts", 1), "INFERRED", "/users", ("GET /u2", "GET /users")),
                  (("show", "src/show.js", 1), "INFERRED", "/a/:id", ("GET /a/:id", "GET /b/:id"))], got)
    cond = {"package.json": ('{ "name": "svc", "imports": { "#config": { "node": "./src/config.node.ts", '
                             '"default": "./src/config.browser.ts" }, '
                             '"#db": { "import": "./dist/db.mjs", "types": "./src/db.ts" } } }\n'),
            "src/config.node.ts": "export class Config {}\n", "src/config.browser.ts": "export class Config {}\n",
            "src/db.ts": "export class Db {}\n", "legacy/db.ts": "export class Db {}\n",
            "packages/cfg/package.json": ('{ "name": "@acme/cfg", "exports": { ".": { "node": "./src/node.ts", '
                                          '"default": "./src/index.ts" } } }\n'),
            "packages/cfg/src/node.ts": "export class Settings {}\n",
            "packages/cfg/src/index.ts": "export class Settings {}\n",
            "src/models.ts": ("import { Config } from '#config';\nimport { Db } from '#db';\nimport { Settings } from '@acme/cfg';\n"
                              "export class Svc extends Config {}\nexport class Repo extends Db {}\n"
                              "export class Prefs extends Settings {}\n")}
    _ed, rows = _tree(base, "conditions", cond)
    check("a package.json conditions object (`imports`, a workspace package's `exports`) is read in its own key "
          "order, Node's rule (`{ node, default }` -> the node file), a target the tree does not hold (`./dist/...`) "
          "giving way to the next one",
          rows == [("INHERITS", ("Prefs", "src/models.ts"), ("Settings", "packages/cfg/src/node.ts"), "INFERRED"),
                   ("INHERITS", ("Repo", "src/models.ts"), ("Db", "src/db.ts"), "INFERRED"),
                   ("INHERITS", ("Svc", "src/models.ts"), ("Config", "src/config.node.ts"), "INFERRED")], rows)
    same = {"src/base.ts": "export class BaseEntity {}\n",
            "src/user.ts": "import { BaseEntity as Base } from './base';\nexport class BaseEntity extends Base {}\n",
            "src/users.service.ts": "import { Injectable } from '@nestjs/common';\n@Injectable()\nexport class UsersService {}\n",
            "src/wrap.service.ts": ("import { Injectable } from '@nestjs/common';\n"
                                    "import { UsersService as Inner } from './users.service';\n@Injectable()\n"
                                    "export class UsersService {\n  constructor(private readonly inner: Inner) {}\n}\n"),
            "src/user.entity.ts": "import { Entity } from 'typeorm';\n@Entity()\nexport class User {}\n",
            "src/photo.entity.ts": ("import { Entity, ManyToOne } from 'typeorm';\nimport { User as Owner } from './user.entity';\n"
                                    "class User {}\n@Entity()\nexport class Photo {\n"
                                    "  @ManyToOne(() => Owner, { onDelete: 'CASCADE' })\n  owner: Owner;\n}\n"),
            "src/loop.ts": "export class Loop extends Loop {}\n"}
    _ed, rows = _tree(base, "alias_same_name", same)
    check("an import aliased because the file declares a class of its name (`BaseEntity as Base`, `UsersService as "
          "Inner`, `User as Owner` beside a local `class User`) binds the imported class: INHERITS, DEPENDS_ON, and "
          "the onDelete CASCADES from the imported principal; a class extending its own name binds nothing",
          rows == [("CASCADES", ("User", "src/user.entity.ts"), ("Photo", "src/photo.entity.ts"), "INFERRED"),
                   ("DEPENDS_ON", ("UsersService", "src/wrap.service.ts"), ("UsersService", "src/users.service.ts"),
                    "INFERRED"),
                   ("INHERITS", ("BaseEntity", "src/user.ts"), ("BaseEntity", "src/base.ts"), "INFERRED"),
                   ("REFERENCES", ("Photo", "src/photo.entity.ts"), ("User", "src/user.entity.ts"), "INFERRED")], rows)
    _member_objects(base, cjs_head)


def _member_objects(base: Path, cjs_head: str) -> None:
    """R6, continued: a member of a module object binds what the module exports under that name, whatever shape
    its default / `=` export has, or nothing — never a private function of the member's name."""
    ts_head = "import { Router } from 'express';\n"
    helper = "function list(items) {\n  return items.slice(0, 10);\n}\n\n"
    cls = "class UsersController {\n  list(req, res) {\n    res.json(list([]));\n  }\n}\n"
    stat = "class UsersController {\n  static list(req, res) {\n    res.json(list([]));\n  }\n}\n"
    inst = {"src/a/users.controller.js": helper + cls + "module.exports = new UsersController();\n",
            "src/b/users.controller.ts": helper + cls + "export default new UsersController();\n",
            "src/c/users.controller.ts": helper + "export default " + stat,
            "src/d/users.controller.js": helper + stat + "module.exports = UsersController;\n",
            "src/e/users.controller.ts": helper + cls + "const users = new UsersController();\nexport default users;\n",
            "src/e/index.ts": "export { default as users } from './users.controller';\n",
            "src/f/UsersController.js": "class UsersController {\n  list(req, res) {}\n}\nmodule.exports = UsersController;\n",
            "src/f/users.js": ("const UsersController = require('./UsersController');\nfunction list(items) {\n  return items;\n}\n"
                               "module.exports = new UsersController();\n"),
            "src/g/users.js": ("function UsersController() {}\nUsersController.prototype.list = function (req, res) {};\n"
                               "function list(items) {\n  return items;\n}\nmodule.exports = new UsersController();\n"),
            "src/h/ctrls.ts": "export class UsersController {\n  list(req, res) {}\n}\n",
            "src/h/users.ts": ("import * as ctrls from './ctrls';\nfunction list(items) {\n  return items;\n}\n"
                               "export default new ctrls.UsersController();\n"),
            "src/i/users.js": "module.exports = class {\n  static list(req, res) {}\n};\n",
            "src/j/base.ts": "export class Base {}\n",
            "src/j/users.ts": "import { Base } from './base';\nexport default class extends Base {\n  static list(req, res) {}\n}\n",
            "src/k/users.ts": "const UsersController = class {\n  static list(req, res) {}\n};\nexport default UsersController;\n",
            "src/l/users.js": (helper + "module.exports = class UsersController {\n  static list(req, res) {\n"
                               "    res.json(list([]));\n  }\n};\n"),
            "src/m/users.js": ("module.exports = class {\n  static list(req, res) {\n    res.send(list([]));\n  }\n};\n"
                               "function list(items) {\n  return items;\n}\n"),
            "src/routes.js": (cjs_head + "const a = require('./a/users.controller');\nconst d = require('./d/users.controller');\n"
                              "const f = require('./f/users');\nconst g = require('./g/users');\nconst i = require('./i/users');\n"
                              "const l = require('./l/users');\nconst m = require('./m/users');\nrouter.get('/a', a.list);\n"
                              "router.get('/d', d.list);\nrouter.get('/f', f.list);\nrouter.get('/g', g.list);\n"
                              "router.get('/i', i.list);\nrouter.get('/l', l.list);\nrouter.get('/m', m.list);\n"
                              "module.exports = router;\n"),
            "src/routes2.ts": (ts_head + "import b from './b/users.controller';\nimport C from './c/users.controller';\n"
                               "import { users } from './e';\nimport h from './h/users';\nimport j from './j/users';\n"
                               "import k from './k/users';\nconst router = Router();\n"
                               "router.get('/b', b.list);\nrouter.get('/c', C.list);\nrouter.get('/e', users.list);\n"
                               "router.get('/h', h.list);\nrouter.get('/j', j.list);\nrouter.get('/k', k.list);\n"
                               "export default router;\n")}
    ed, _rows = _tree(base, "member_classes", inst)
    mods = [("routes", "src/routes.js", "Module", 1), ("routes2", "src/routes2.ts", "Module", 1)]
    # a class EXPRESSION's methods are bare functions to the engine, one node per name in a file, the first: l keeps
    # its helper, m its method
    nodes = mods + [n for d in "abcde" for n in (
        ("list", f"src/{d}/users.controller.{'js' if d in 'ad' else 'ts'}", "Function", 1),
        ("UsersController.list", f"src/{d}/users.controller.{'js' if d in 'ad' else 'ts'}", "Method", 6))] + [
        ("UsersController.list", "src/f/UsersController.js", "Method", 2), ("list", "src/f/users.js", "Function", 2),
        ("list", "src/g/users.js", "Function", 3), ("UsersController.list", "src/h/ctrls.ts", "Method", 2),
        ("list", "src/h/users.ts", "Function", 2), ("list", "src/i/users.js", "Function", 2),
        ("list", "src/j/users.ts", "Function", 3), ("list", "src/k/users.ts", "Function", 2),
        ("list", "src/l/users.js", "Function", 1), ("list", "src/m/users.js", "Function", 2)]
    got = {ver: _route_rows(ed, nodes + extra) for ver, extra in (
        ("0.8.39", []), ("0.8.51", [("UsersController.list", "src/g/users.js", "Method", 2)]))}
    want = [(("UsersController.list", f"src/{d}/users.controller.{'js' if d in 'ad' else 'ts'}", 6), "INFERRED",
             f"/{d}", ()) for d in "abcde"] + [(("UsersController.list", "src/f/UsersController.js", 2), "INFERRED", "/f", ()),
                                               (("UsersController.list", "src/h/ctrls.ts", 2), "INFERRED", "/h", ()),
                                               (("list", "src/i/users.js", 2), "INFERRED", "/i", ()),
                                               (("list", "src/j/users.ts", 3), "INFERRED", "/j", ()),
                                               (("list", "src/k/users.ts", 2), "INFERRED", "/k", ()),
                                               (("list", "src/m/users.js", 2), "INFERRED", "/m", ())]
    check("a module object whose default / `=` export is an instance (`new X()`, also held in a const or behind a "
          "barrel), a class (its static method) or an instance of an imported class (`new ctrls.X()` through a "
          "namespace too) or a constructor function binds "
          "that class's method; a class expression (`module.exports = class { ... }`, `export default class extends "
          "Base`, `const X = class { ... }`) binds its method, which the engine names bare, at its line — "
          "never the module's private `function list` of the member's name",
          got == {"0.8.39": sorted(want),
                  "0.8.51": sorted(want + [(("UsersController.list", "src/g/users.js", 2), "INFERRED", "/g", ())])}, got)
    _member_heritage(base, cjs_head, ts_head)
    fns ="function login(req, res) {\n  res.redirect('/');\n}\nfunction renderLogin(req, res) {\n  res.render('login');\n}\n"
    ctrl = "const controller = { login: renderLogin, session: login };\n"
    objd = {"src/a/users.js": fns + ctrl + "module.exports = controller;\n",
            "src/b/users.ts": fns + ctrl + "export default controller;\n",
            "src/c/users.ts": fns + "export default { login: renderLogin, session: login };\n",
            "src/d/users.js": fns + "module.exports = { login: renderLogin, session: login };\n",
            "src/e/users.js": fns + "const controller = {};\ncontroller.session = login;\nmodule.exports = controller;\n",
            "src/f/users.ts": fns + "export default {\n  session: login,\n  async show(req, res) {},\n};\n",
            "src/g/users.js": fns + "module.exports = { session: login };\nmodule.exports.edit = renderLogin;\n",
            "src/h/users.js": fns + "const controller = {};\ncontroller.login = function (req, res) {};\nmodule.exports = controller;\n",
            "src/i/users.ts": fns + "export default {\n  login(req, res) {},\n};\n",
            "src/j/index.ts": "import * as impl from './impl';\nexport default impl;\n",
            "src/j/impl.ts": "export function login(req, res) {}\nexport function session(req, res) {}\n",
            "src/k/users.js": fns + "module.exports = function (req, res) {};\nmodule.exports.session = login;\n",
            "src/l/users.js": (fns + "const controller = { session: login };\nclass Cache {\n  constructor() {\n"
                               "    this.controller = {};\n    this.controller.login = renderLogin;\n  }\n}\n"
                               "module.exports = controller;\n"),
            "src/m/users.js": (fns + "function make() {\n  const controller = { login: renderLogin };\n  return controller;\n}\n"
                               "const controller = { session: login };\nmodule.exports = controller;\n"),
            "src/n/users.js": fns + "const admin = { session: login };\nexports.admin = admin;\nexports.login = renderLogin;\n",
            "src/o/users.ts": fns + "export const usersController = { login: renderLogin, session: login };\n",
            "src/routes.js": (cjs_head + "".join(f"const {d} = require('./{d}/users');\n" for d in "adeghklm")
                              + "const { admin: n } = require('./n/users');\n"
                              + "".join(f"router.get('/{d}/login', {d}.login);\nrouter.post('/{d}/session', {d}.session);\n"
                                        for d in "adeghklmn") + "router.get('/g/edit', g.edit);\nmodule.exports = router;\n"),
            "src/routes2.ts": (ts_head + "".join(f"import {d} from './{d}/users';\n" for d in "bcfi") + "import j from './j';\n"
                               + "import { usersController as o } from './o/users';\nconst router = Router();\n"
                               + "".join(f"router.get('/{d}/login', {d}.login);\nrouter.post('/{d}/session', {d}.session);\n"
                                         for d in "bcfijo") + "router.get('/f/show', f.show);\nexport default router;\n")}
    ed, _rows = _tree(base, "member_objects", objd)
    ext = {d: "ts" if d in "bcfio" else "js" for d in "abcdefghiklmno"}
    nodes = mods + [n for d in "abcdefghiklmno" for n in (("login", f"src/{d}/users.{ext[d]}", "Function", 1),
                                                            ("renderLogin", f"src/{d}/users.{ext[d]}", "Function", 4))] + [
        ("show", "src/f/users.ts", "Function", 9), ("login", "src/j/impl.ts", "Function", 1),
        ("session", "src/j/impl.ts", "Function", 2)]
    got = _route_rows(ed, nodes)
    want = sorted([(("renderLogin", f"src/{d}/users.{ext[d]}", 4), "INFERRED", f"/{d}/login", ()) for d in "abcdo"]
                  + [(("login", f"src/{d}/users.{ext[d]}", 1), "INFERRED", f"/{d}/session", ()) for d in "abcdefgklmno"]
                  + [(("show", "src/f/users.ts", 9), "INFERRED", "/f/show", ()),
                     (("renderLogin", "src/g/users.js", 4), "INFERRED", "/g/edit", ()),
                     (("login", "src/j/impl.ts", 1), "INFERRED", "/j/login", ()),
                     (("session", "src/j/impl.ts", 2), "INFERRED", "/j/session", ())])
    check("a module object whose default is an object — a declared const (`module.exports = controller`, `export default "
          "controller`, `export const usersController` through a named import, members assigned later included), an "
          "ESM `export default { login: renderLogin }`, a "
          "`module.exports = { ... }` or a function plus `module.exports.edit = ...`, a namespace held as the default — "
          "binds that object's entries (`login` -> renderLogin, `session` -> login, a method `async show() {}` at its "
          "line); a key the object does not hold, or one it defines at another line than the module's private "
          "`function login` (`controller.login = function`, `{ login() {} }`), never binds that private function; "
          "only the top-level const counts (not `this.controller.login = ...`, not a nested `const controller`), and a "
          "CommonJS key of the module is no member of another object it exports (`exports.admin`)",
          got == want, got)
    pair = "{ login: renderLogin, session: login }"
    chained = {"src/a/users.js": fns + f"module.exports = exports = {pair};\n",
               "src/b/users.js": fns + f"const controller = {pair};\nmodule.exports = exports = controller;\n",
               "src/c/users.js": fns + f"exports = module.exports = {pair};\n",
               "src/d/users.js": fns + "function logout(req, res) {}\nmodule.exports = exports = { login: renderLogin };\n",
               "src/routes.js": (cjs_head + "".join(f"const {d} = require('./{d}/users');\n" for d in "abcd")
                                 + "const { login: loginA } = require('./a/users');\n"
                                 + "".join(f"router.get('/{d}/login', {d}.login);\nrouter.post('/{d}/session', {d}.session);\n"
                                           for d in "abc")
                                 + "router.get('/a2/login', loginA);\nrouter.get('/d/logout', d.logout);\nmodule.exports = router;\n")}
    ed, _rows = _tree(base, "member_chained", chained)
    got = _route_rows(ed, mods[:1] + [n for d in "abcd" for n in (("login", f"src/{d}/users.js", "Function", 1),
                                                                    ("renderLogin", f"src/{d}/users.js", "Function", 4))]
                      + [("logout", "src/d/users.js", "Function", 7)])
    check("the legacy chain `module.exports = exports = { login: renderLogin }` (or `= exports = controller`) is read "
          "as `module.exports = { ... }`, like the reversed `exports = module.exports = { ... }`: `login` -> renderLogin "
          "(also through `const { login } = require(...)`), `session` -> login; a key the object lacks binds nothing — "
          "never the private `function login` / `function logout`",
          got == sorted([(("renderLogin", f"src/{d}/users.js", 4), "INFERRED", f"/{d}/login", ()) for d in "bc"]
                        + [(("renderLogin", "src/a/users.js", 4), "INFERRED", "/a/login", ("GET /a/login", "GET /a2/login"))]
                        + [(("login", f"src/{d}/users.js", 1), "INFERRED", f"/{d}/session", ()) for d in "abc"]), got)
    ctl = ("const asyncHandler = (fn) => fn;\nasync function list(req, res) {\n  res.send([]);\n}\n"
           "async function show(req, res) {\n  res.send({});\n}\n")
    wrapd = {"src/a/ctrl.js": ctl + "module.exports = {\n  list: asyncHandler(list),\n  show: asyncHandler(show),\n};\n",
             "src/b/ctrl.js": (ctl.replace("asyncHandler", "catchAsync")
                               + "exports.list = catchAsync(list);\nexports.show = catchAsync(show);\n"),
             "src/c/ctrl.js": ("function auth(req, res, next) {}\nfunction list(req, res) {}\n"
                               "exports.list = compose(auth, list);\nexports.show = catchAsync(showImpl);\n"),
             "src/d/impl.js": "function list(req, res) {}\nmodule.exports = { list };\n",
             "src/d/ctrl.js": "const { list } = require('./impl');\nexports.list = catchAsync(list);\n",
             "src/e/ctrl.ts": ctl + "export default {\n  list: asyncHandler(list),\n};\n",
             "src/routes.js": (cjs_head + "const a = require('./a/ctrl');\nconst { show: showA } = require('./a/ctrl');\n"
                               "const b = require('./b/ctrl');\nconst { show: showB } = require('./b/ctrl');\n"
                               "const c = require('./c/ctrl');\nconst d = require('./d/ctrl');\nrouter.get('/a', a.list);\n"
                               "router.get('/a/:id', showA);\nrouter.get('/b', b.list);\nrouter.get('/b/:id', showB);\n"
                               "router.get('/c', c.list);\nrouter.get('/c/:id', c.show);\nrouter.get('/d', d.list);\n"
                               "module.exports = router;\n"),
             "src/routes2.ts": (ts_head + "import e from './e/ctrl';\nconst router = Router();\nrouter.get('/e', e.list);\n"
                                "export default router;\n")}
    ed, _rows = _tree(base, "cjs_wrapped_decl", wrapd)
    nodes = [("routes", "src/routes.js", "Module", 1), ("routes2", "src/routes2.ts", "Module", 1),
             ("list", "src/a/ctrl.js", "Function", 2), ("show", "src/a/ctrl.js", "Function", 5),
             ("list", "src/b/ctrl.js", "Function", 2), ("show", "src/b/ctrl.js", "Function", 5),
             ("auth", "src/c/ctrl.js", "Function", 1), ("list", "src/c/ctrl.js", "Function", 2),
             ("list", "src/d/impl.js", "Function", 1), ("list", "src/e/ctrl.ts", "Function", 2)]
    got = _route_rows(ed, nodes)
    strict = [(x.dst_name, x.scope) for x in _e(ed, "ROUTES_TO") if "!" in x.scope and "/c/" not in x.scope]
    check("a CommonJS export wrapping the module's own declared or imported function once (`module.exports = { list: "
          "asyncHandler(list) }`, `exports.list = catchAsync(list)`, an ESM `export default { list: asyncHandler(list) "
          "}` too) is that function: no strict line hint, the routes bind it; `compose(auth, list)` (no wrapper of one "
          "handler) or a wrapped name the module does not declare binds nothing — never `auth`, never the module's "
          "`list`",
          got == [(("list", "src/a/ctrl.js", 2), "INFERRED", "/a", ()), (("list", "src/b/ctrl.js", 2), "INFERRED", "/b", ()),
                  (("list", "src/d/impl.js", 1), "INFERRED", "/d", ()), (("list", "src/e/ctrl.ts", 2), "INFERRED", "/e", ()),
                  (("show", "src/a/ctrl.js", 5), "INFERRED", "/a/:id", ()),
                  (("show", "src/b/ctrl.js", 5), "INFERRED", "/b/:id", ())] and strict == [], f"{got} {strict}")
    multi = {"app/controllers/users.js": ("exports.login =\n  function (req, res) {\n    res.render('users/login');\n  };\n"
                                          "exports.session = login;\nfunction login(req, res) {\n  res.redirect('/');\n}\n"),
             "app/controllers/obj.js": "function list(req, res) {}\nmodule.exports = {\n  list:\n    async (req, res) => {},\n};\n",
             "config/routes.js": ("const users = require('../app/controllers/users');\nconst obj = require('../app/controllers/obj');\n"
                                  "module.exports = function (app) {\n  app.get('/login', users.login);\n"
                                  "  app.post('/users/session', users.session);\n  app.get('/list', obj.list);\n};\n")}
    ed, _rows = _tree(base, "madhums_multiline", multi)
    rmod = ("routes", "config/routes.js", "Module", 1)
    got = {ver: _route_rows(ed, [rmod, ("login", "app/controllers/users.js", "Function", line),
                                 ("list", "app/controllers/obj.js", "Function", 1)])
           for ver, line in (("0.8.39", 6), ("0.8.51", 1))}
    lst = [x.scope for x in _e(ed, "ROUTES_TO") if x.attrs.get("path") == "/list"]
    check("an `exports.login =` whose function starts on the next line is defined at the `exports.login` line, the line "
          "the engine gives it: GET /login binds 0.8.51's merged `login` node there; POST /users/session binds the "
          "private `function login` (0.8.39); an object's `list:` entry is defined at its key's line (strict beside "
          "a `function list`: never that function)",
          got == {"0.8.39": [(("login", "app/controllers/users.js", 6), "INFERRED", "/users/session", ())],
                  "0.8.51": [(("login", "app/controllers/users.js", 1), "INFERRED", "/login", ())]}
          and lst == ["L0,3!|exact:module:app/controllers/obj"], f"{got} {lst}")
    keep = {"src/k1/users.js": ("const catchAsync = (fn) => fn;\nexports.list = catchAsync(async (req, res) => {\n"
                                "  res.send([]);\n});\n"),
            "src/k2/user.controller.js": ("const catchAsync = require('../utils/catchAsync');\n\n"
                                          "const createUser = catchAsync(async (req, res) => {\n  res.send({});\n});\n\n"
                                          "const getUsers = catchAsync(async (req, res) => {\n  res.send([]);\n});\n\n"
                                          "module.exports = {\n  createUser,\n  getUsers,\n};\n"),
            "src/utils/catchAsync.js": "module.exports = (fn) => (req, res, next) => {\n  fn(req, res, next);\n};\n",
            "src/k3/users.js": "function list(req, res) {}\nmodule.exports = { list };\n",
            "src/k5/users.controller.js": "class UsersController {\n  list(req, res) {}\n}\nmodule.exports = new UsersController();\n",
            "src/k6/users.ts": "function list(req, res) {}\nexport default { list };\n",
            "src/k7/users.ts": "export function list(req, res) {}\n",
            "src/routes.js": (cjs_head + "const k1 = require('./k1/users');\nconst k2 = require('./k2/user.controller');\n"
                              "const k3 = require('./k3/users');\nconst k5 = require('./k5/users.controller');\n"
                              "router.get('/k1', k1.list);\nrouter.route('/k2').post(k2.createUser).get(k2.getUsers);\n"
                              "router.get('/k3', k3.list);\nrouter.get('/k5', k5.list);\nmodule.exports = router;\n"),
            "src/routes2.ts": (ts_head + "import k6 from './k6/users';\nimport { list } from './k7/users';\n"
                               "import * as k7 from './k7/users';\nconst router = Router();\nrouter.get('/k6', k6.list);\n"
                               "router.get('/k7', list);\nrouter.get('/k7b', k7.list);\nexport default router;\n")}
    ed, _rows = _tree(base, "member_keep", keep)
    got = _route_rows(ed, mods + [("createUser", "src/k2/user.controller.js", "Function", 3),
                                  ("getUsers", "src/k2/user.controller.js", "Function", 7),
                                  ("list", "src/k3/users.js", "Function", 1),
                                  ("UsersController.list", "src/k5/users.controller.js", "Method", 2),
                                  ("list", "src/k6/users.ts", "Function", 1), ("list", "src/k7/users.ts", "Function", 1)])
    k1 = [x.scope for x in _e(ed, "ROUTES_TO") if x.attrs.get("path") == "/k1"]
    check("still bound as before: `exports.list = catchAsync(async ...)` at its own line (the engine's node there or "
          "nothing), the hagopj13 `const createUser = catchAsync(...)` + `module.exports = { createUser }`, "
          "`module.exports = { list }`, an instance's method, `export default { list }`, and a named import folded "
          "with a namespace member of one module (madhums: the check above)",
          got == [(("UsersController.list", "src/k5/users.controller.js", 2), "INFERRED", "/k5", ()),
                  (("createUser", "src/k2/user.controller.js", 3), "INFERRED", "/k2", ()),
                  (("getUsers", "src/k2/user.controller.js", 7), "INFERRED", "/k2", ()),
                  (("list", "src/k3/users.js", 1), "INFERRED", "/k3", ()),
                  (("list", "src/k6/users.ts", 1), "INFERRED", "/k6", ()),
                  (("list", "src/k7/users.ts", 1), "INFERRED", "/k7", ("GET /k7", "GET /k7b"))]
          and k1 == ["L0,2|exact:module:src/k1/users"], f"{got} {k1}")
    unread = {"src/controllers/users.js": ("const catchAsync = (fn) => fn;\nfunction show(req, res) {}\n"
                                           "Object.assign(module.exports, {\n  show,\n"
                                           "  list: catchAsync(async (req, res) => { res.send([]); }),\n});\n"),
              "src/controllers/orders.js": "function list(req, res) {}\nfunction show(req, res) {}\nmodule.exports = { list, show };\n",
              "src/routes.js": (cjs_head + "const { list: listUsers, show } = require('./controllers/users');\n"
                                "router.get('/users', listUsers);\nrouter.get('/users/:id', show);\nmodule.exports = router;\n"),
              "src/crud.ts": "export function crud(name: string) {\n  return { list: (req: any, res: any) => {} };\n}\n",
              "src/users.controller.ts": "import { crud } from './crud';\nexport const { list, show: view } = crud('users');\n",
              "src/orders.controller.ts": "export function list(req: any, res: any) {}\nexport function view(req: any, res: any) {}\n",
              "src/routes2.ts": (ts_head + "import { list, view } from './users.controller';\nconst router = Router();\n"
                                 "router.get('/u2', list);\nrouter.get('/u2/:id', view);\nexport default router;\n")}
    ed, _rows = _tree(base, "export_shapes", unread)
    got = _route_rows(ed, [("routes", "src/routes.js", "Module", 1), ("routes2", "src/routes2.ts", "Module", 1),
                           ("show", "src/controllers/users.js", "Function", 2),
                           ("list", "src/controllers/orders.js", "Function", 1),
                           ("show", "src/controllers/orders.js", "Function", 2),
                           ("list", "src/orders.controller.ts", "Function", 1),
                           ("view", "src/orders.controller.ts", "Function", 2)])
    check("`Object.assign(module.exports, { show, list: ... })` and a destructured `export const { list, show: view } = "
          "...` define those exports in their module: `show` binds users.js's own function; `list` / `view` bind a "
          "node there or nothing — never another module's same-named handler",
          got == [(("show", "src/controllers/users.js", 2), "INFERRED", "/users/:id", ())], got)
    typed = {"src/crud.ts": unread["src/crud.ts"],
             "src/m8/users.ts": ("import { crud } from '../crud';\n"
                                 "export const { show = (req: Request, res: Response) => {} } = crud('users');\n"),
             "src/m9/users.ts": ("import { crud } from '../crud';\n"
                                 "export const { list, show = (req: any, res: any) => {} } = crud('users');\n"),
             "src/orders.ts": "export function show(req: any, res: any) {}\nexport function list(req: any, res: any) {}\n",
             "src/routes2.ts": (ts_head + "import { show } from './m8/users';\nimport { list as l9, show as s9 } from './m9/users';\n"
                                "const router = Router();\nrouter.get('/m8/:id', show);\nrouter.get('/m9', l9);\n"
                                "router.get('/m9/:id', s9);\nexport default router;\n")}
    ed, _rows = _tree(base, "export_typed_defaults", typed)
    got = _route_rows(ed, [("routes2", "src/routes2.ts", "Module", 1), ("show", "src/orders.ts", "Function", 1),
                           ("list", "src/orders.ts", "Function", 2)])
    names = [sorted(T._File(src, "src/x.ts").prop_defs) for src in (
        "export const { show = flag ? a : b } = make();\n", "export const { a: b = 1, c = { d: 1 } } = make();\n",
        "export const { list, show = (req: Request) => {} } = make();\n")]
    check("a destructured export whose default holds a `:` (a typed arrow parameter, a ternary) keeps its own name "
          "(`show`, never `Request` / `b`): an import of it binds a node there or nothing, never another module's "
          "same-named handler; `{ a: b = 1 }` still binds `b`",
          got == [] and names == [["show"], ["b", "c"], ["list", "show"]], f"{got} {names}")


def _member_heritage(base: Path, cjs_head: str, ts_head: str) -> None:
    """R6, continued: a member read on a class, or on an instance of one, binds the method of the class in its
    `extends` chain that holds it (in its module or an imported one; a member the class sets itself in its code counts);
    a class expression's method is the engine's bare one at its line. The node lists are the engines' own (0.8.51 adds
    o8's and the nodes a constructor's `this.x = ...` or a prototype assignment defines)."""
    bcls = "class BaseController {\n  list(req, res) {\n    res.json([]);\n  }\n}\n"
    expr = "const UsersController = class {\n  list(req, res) {\n    res.json([]);\n  }\n};\n"
    helper = "function list(items) {\n  return items;\n}\n"
    her = {"src/a2/users.js": (bcls + "class UsersController extends BaseController {\n  show(req, res) {\n"
                               "    res.json({});\n  }\n}\nmodule.exports = new UsersController();\n"),
           "src/m5/users.ts": bcls + "class UsersController extends BaseController {}\nexport default new UsersController();\n",
           "src/a6/users.ts": ("class Base {\n  static list(req, res) {\n    res.json([]);\n  }\n}\n"
                               "export default class UsersController extends Base {\n  static show(req, res) {\n"
                               "    res.json({});\n  }\n}\n"),
           "src/a1/users.js": expr + "module.exports = new UsersController();\n",
           "src/m7/users.ts": expr + "export default new UsersController();\n",
           "src/m6/base.js": "class BaseController {\n  list(req, res) {}\n}\nmodule.exports = BaseController;\n",
           "src/m6/users.js": ("const BaseController = require('./base');\n" + helper
                               + "class UsersController extends BaseController {}\nmodule.exports = new UsersController();\n"),
           "src/a3/UsersController.js": "module.exports = class UsersController {\n  list(req, res) {}\n};\n",
           "src/a3/users.js": "const UsersController = require('./UsersController');\nmodule.exports = new UsersController();\n",
           "src/o1/users.ts": ("class Base {\n  list(req, res) {}\n}\nclass UsersController extends Base {\n  list(req, res) {}\n}\n"
                               "export default new UsersController();\n"),
           "src/o2/users.ts": (helper + "class Base {\n  list(req, res) {}\n}\nclass Mid extends Base {}\n"
                               "class UsersController extends Mid {}\nexport default new UsersController();\n"),
           "src/o3/users.ts": ("import { Controller } from 'tsoa';\n" + helper
                               + "class UsersController extends Controller {}\nexport default new UsersController();\n"),
           "src/o4/users.ts": helper + "class A extends B {}\nclass B extends A {}\nexport default new A();\n",
           "src/o5/base.ts": "export class Base {\n  list(req, res) {}\n}\n",
           "src/o5/users.ts": ("import * as b from './base';\nclass UsersController extends b.Base {}\n"
                               "export default new UsersController();\n"),
           "src/o6/users.js": ("class Base {\n  static list(req, res) {}\n}\nmodule.exports = class extends Base {\n"
                               "  static show(req, res) {}\n};\n"),
           "src/o7/users.js": ("const Base = class {\n  list(req, res) {}\n};\nclass UsersController extends Base {}\n"
                               "module.exports = new UsersController();\n"),
           "src/o8/users.js": ("function Base() {}\nBase.prototype.list = function (req, res) {};\n"
                               "class UsersController extends Base {}\nmodule.exports = new UsersController();\n"),
           "src/o9/base.ts": "export default class BaseController {\n  static list(req, res) {}\n}\n",
           "src/o9/users.ts": "import Base from './base';\n" + helper + "export default class UsersController extends Base {}\n",
           "src/o10/users.ts": ("class Base {\n  static list(req, res) {}\n}\n"
                                "const Users = class Named<T extends object> extends Base {};\nexport default Users;\n"),
           "src/o11/users.js": ("class Base {\n  list(req, res) {}\n}\nconst UsersController = class extends Base {};\n"
                                "module.exports = new UsersController();\n"),
           "src/o12/users.js": "const Base = class {\n  static list(req, res) {}\n};\nmodule.exports = class extends Base {};\n",
           "src/o13/users.ts": "import * as b from '../o5/base';\nconst B = class extends b.Base {};\nexport default new B();\n",
           "src/o14/users.js": expr + "const users = new UsersController();\nmodule.exports = users;\n",
           "src/o15/users.js": (helper + "function renderList(req, res) {}\nconst UsersController = class {};\n"
                                "UsersController.list = renderList;\nmodule.exports = UsersController;\n"),
           "src/routes.js": (cjs_head + "".join(f"const {d} = require('./{d}/users');\n"
                                                for d in ("a2", "a1", "m6", "a3", "o6", "o7", "o8", "o11", "o12", "o14", "o15"))
                             + "router.get('/a2', a2.list);\nrouter.get('/a2/:id', a2.show);\nrouter.get('/a1', a1.list);\n"
                             "router.get('/m6', m6.list);\nrouter.get('/a3', a3.list);\nrouter.get('/o6', o6.list);\n"
                             "router.get('/o6/:id', o6.show);\nrouter.get('/o7', o7.list);\nrouter.get('/o8', o8.list);\n"
                             "router.get('/o11', o11.list);\nrouter.get('/o12', o12.list);\nrouter.get('/o14', o14.list);\n"
                             "router.get('/o15', o15.list);\nmodule.exports = router;\n"),
           "src/routes2.ts": (ts_head + "import m5 from './m5/users';\nimport A6 from './a6/users';\nimport m7 from './m7/users';\n"
                              + "".join(f"import {d} from './{d}/users';\n" for d in ("o1", "o2", "o3", "o4", "o5", "o10", "o13"))
                              + "import O9 from './o9/users';\nconst router = Router();\n"
                              "router.get('/m5', m5.list);\nrouter.get('/a6', A6.list);\nrouter.get('/a6/:id', A6.show);\n"
                              "router.get('/m7', m7.list);\nrouter.get('/o1', o1.list);\nrouter.get('/o2', o2.list);\n"
                              "router.get('/o3', o3.list);\nrouter.get('/o4', o4.list);\nrouter.get('/o5', o5.list);\n"
                              "router.get('/o9', O9.list);\nrouter.get('/o10', o10.list);\nrouter.get('/o13', o13.list);\n"
                              "export default router;\n")}
    ed, _rows = _tree(base, "member_heritage", her)
    nodes = [("routes", "src/routes.js", "Module", 1), ("routes2", "src/routes2.ts", "Module", 1)] + [
        (nm, f"src/{f}", lb, ln) for f, ln, lb, nm in (
            ("a1/users.js", 2, "Function", "list"), ("a2/users.js", 1, "Class", "BaseController"),
            ("a2/users.js", 2, "Method", "BaseController.list"), ("a2/users.js", 6, "Class", "UsersController"),
            ("a2/users.js", 7, "Method", "UsersController.show"), ("a3/UsersController.js", 2, "Function", "list"),
            ("a3/users.js", 1, "Class", "UsersController"), ("a6/users.ts", 1, "Class", "Base"),
            ("a6/users.ts", 2, "Method", "Base.list"), ("a6/users.ts", 6, "Class", "UsersController"),
            ("a6/users.ts", 7, "Method", "UsersController.show"), ("m5/users.ts", 1, "Class", "BaseController"),
            ("m5/users.ts", 2, "Method", "BaseController.list"), ("m5/users.ts", 6, "Class", "UsersController"),
            ("m6/base.js", 1, "Class", "BaseController"), ("m6/base.js", 2, "Method", "BaseController.list"),
            ("m6/users.js", 1, "Class", "BaseController"), ("m6/users.js", 2, "Function", "list"),
            ("m6/users.js", 5, "Class", "UsersController"), ("m7/users.ts", 2, "Function", "list"),
            ("o1/users.ts", 1, "Class", "Base"), ("o1/users.ts", 2, "Method", "Base.list"),
            ("o1/users.ts", 4, "Class", "UsersController"), ("o1/users.ts", 5, "Method", "UsersController.list"),
            ("o2/users.ts", 1, "Function", "list"), ("o2/users.ts", 4, "Class", "Base"), ("o2/users.ts", 5, "Method", "Base.list"),
            ("o2/users.ts", 7, "Class", "Mid"), ("o2/users.ts", 8, "Class", "UsersController"),
            ("o3/users.ts", 2, "Function", "list"), ("o3/users.ts", 5, "Class", "UsersController"),
            ("o4/users.ts", 1, "Function", "list"), ("o4/users.ts", 4, "Class", "A"), ("o4/users.ts", 5, "Class", "B"),
            ("o5/base.ts", 1, "Class", "Base"), ("o5/base.ts", 2, "Method", "Base.list"),
            ("o5/users.ts", 2, "Class", "UsersController"), ("o6/users.js", 1, "Class", "Base"),
            ("o6/users.js", 2, "Method", "Base.list"), ("o6/users.js", 5, "Function", "show"),
            ("o7/users.js", 2, "Function", "list"), ("o7/users.js", 4, "Class", "UsersController"),
            ("o8/users.js", 3, "Class", "UsersController"), ("o9/base.ts", 1, "Class", "BaseController"),
            ("o9/base.ts", 2, "Method", "BaseController.list"), ("o9/users.ts", 2, "Function", "list"),
            ("o9/users.ts", 5, "Class", "UsersController"), ("o10/users.ts", 1, "Class", "Base"),
            ("o10/users.ts", 2, "Method", "Base.list"), ("o11/users.js", 1, "Class", "Base"),
            ("o11/users.js", 2, "Method", "Base.list"), ("o12/users.js", 2, "Function", "list"),
            ("o14/users.js", 2, "Function", "list"), ("o14/users.js", 4, "Class", "users"),
            ("o15/users.js", 1, "Function", "list"), ("o15/users.js", 4, "Function", "renderList"))]
    got = {ver: _route_rows(ed, nodes + extra) for ver, extra in (
        ("0.8.39", [("Base", "src/o8/users.js", "Function", 1)]),
        ("0.8.51", [("Base", "src/o8/users.js", "Class", 1), ("Base.list", "src/o8/users.js", "Method", 2)]))}
    restored = ("/a2", "/a2/:id", "/m5", "/a6", "/a6/:id", "/a1", "/m7", "/o14")
    want = sorted([(("BaseController.list", "src/a2/users.js", 2), "INFERRED", "/a2", ()),
                   (("UsersController.show", "src/a2/users.js", 7), "INFERRED", "/a2/:id", ()),
                   (("BaseController.list", "src/m5/users.ts", 2), "INFERRED", "/m5", ()),
                   (("Base.list", "src/a6/users.ts", 2), "INFERRED", "/a6", ()),
                   (("UsersController.show", "src/a6/users.ts", 7), "INFERRED", "/a6/:id", ()),
                   (("list", "src/a1/users.js", 2), "INFERRED", "/a1", ()), (("list", "src/m7/users.ts", 2), "INFERRED", "/m7", ()),
                   (("list", "src/o14/users.js", 2), "INFERRED", "/o14", ())])
    check("a member of an instance or a class whose class inherits it binds the base's method (`new UsersController()` "
          "of `class UsersController extends BaseController`, CommonJS and ESM; a static `Base.list` through `export default "
          "class UsersController extends Base`); a member of `new X()` where `const X = class { ... }` (also held in a "
          "const) binds the class expression's method, which the engine names bare, at its line",
          {v: [r for r in rows if r[2] in restored] for v, rows in got.items()} == {"0.8.39": want, "0.8.51": want}, got)
    chain = sorted([(("BaseController.list", "src/m6/base.js", 2), "INFERRED", "/m6", ()),
                    (("list", "src/a3/UsersController.js", 2), "INFERRED", "/a3", ()),
                    (("UsersController.list", "src/o1/users.ts", 5), "INFERRED", "/o1", ()),
                    (("Base.list", "src/o2/users.ts", 5), "INFERRED", "/o2", ()),
                    (("Base.list", "src/o5/base.ts", 2), "INFERRED", "/o5", ("GET /o13", "GET /o5")),
                    (("Base.list", "src/o6/users.js", 2), "INFERRED", "/o6", ()),
                    (("show", "src/o6/users.js", 5), "INFERRED", "/o6/:id", ()),
                    (("list", "src/o7/users.js", 2), "INFERRED", "/o7", ()),
                    (("BaseController.list", "src/o9/base.ts", 2), "INFERRED", "/o9", ()),
                    (("Base.list", "src/o10/users.ts", 2), "INFERRED", "/o10", ()),
                    (("Base.list", "src/o11/users.js", 2), "INFERRED", "/o11", ()),
                    (("list", "src/o12/users.js", 2), "INFERRED", "/o12", ()),
                    (("renderList", "src/o15/users.js", 4), "INFERRED", "/o15", ())])
    check("the `extends` chain is followed to the class that declares the member: an imported base (require, a default or "
          "a namespace import `b.Base`), two levels (`UsersController extends Mid extends Base`), a class expression's "
          "base (also a named generic one, a const one, a dotted one) and a `const Base = class { ... }` base (its bare "
          "method), a constructor function's prototype method (0.8.51's `Base.list`), an imported class-expression "
          "constructor, a class expression's member assigned later (`X.list = renderList`); an override binds the "
          "subclass's method; an external base (`extends Controller` from tsoa) or a cycle binds nothing — never the "
          "module's private `function list`",
          {v: [r for r in rows if r[2] not in restored] for v, rows in got.items()} == {
              "0.8.39": chain, "0.8.51": sorted(chain + [(("Base.list", "src/o8/users.js", 2), "INFERRED", "/o8", ())])}, got)
    # a member the subclass sets itself, outside its body's methods, overrides the base's (h31: a CRUD base whose
    # constructor self-binds its methods, a subclass whose constructor assigns `this.show`)
    own = {"src/h6/users.js": (bcls + "class UsersController extends BaseController {\n  constructor() {\n    super();\n"
                               "    this.list = (req, res) => {\n      res.json(['users']);\n    };\n  }\n}\n"
                               "module.exports = new UsersController();\n"),
           "src/h6b/users.js": (bcls + "class UsersController extends BaseController {\n  constructor() {\n    super();\n"
                                "    this.list = this.list.bind(this);\n  }\n}\nmodule.exports = new UsersController();\n"),
           "src/h8/users.js": ("class Base {\n  static list(req, res) {}\n}\nclass UsersController extends Base {}\n"
                               "UsersController.list = function (req, res) {};\nmodule.exports = UsersController;\n"),
           "src/h8b/users.js": ("class Base {\n  list(req, res) {}\n}\nclass UsersController extends Base {}\n"
                                "UsersController.prototype.list = function (req, res) {};\nmodule.exports = new UsersController();\n"),
           "src/h26/users.js": ("function renderList(req, res) {}\nclass Base {\n  static list(req, res) {}\n}\n"
                                "class UsersController extends Base {}\nUsersController.list = renderList;\n"
                                "module.exports = UsersController;\n"),
           "src/h28/users.js": (bcls + "function listUsers(req, res) {}\nclass UsersController extends BaseController {\n"
                                "  constructor() {\n    super();\n    this.list = listUsers;\n  }\n}\n"
                                "module.exports = new UsersController();\n"),
           "src/h31/users.js": ("class CrudController {\n  constructor(model) {\n    this.model = model;\n"
                                "    this.list = this.list.bind(this);\n    this.show = this.show.bind(this);\n  }\n"
                                "  async list(req, res) {\n    res.json([]);\n  }\n  async show(req, res) {\n    res.json({});\n  }\n}\n"
                                "class UsersController extends CrudController {\n  constructor() {\n    super('User');\n"
                                "    this.show = async (req, res) => {\n      res.json({ custom: true });\n    };\n  }\n}\n"
                                "module.exports = new UsersController();\n")}
    dirs = ("h6", "h6b", "h8", "h8b", "h26", "h28", "h31")
    own["src/routes.js"] = (cjs_head + "".join(f"const {d} = require('./{d}/users');\n" for d in dirs)
                            + "".join(f"router.get('/{d}', {d}.list);\n" for d in dirs)
                            + "router.get('/h31/:id', h31.show);\nmodule.exports = router;\n")
    ed, _rows = _tree(base, "member_overrides", own)
    nodes = [("routes", "src/routes.js", "Module", 1)] + [
        (nm, f"src/{f}", lb, ln) for f, ln, lb, nm in (
            ("h6/users.js", 1, "Class", "BaseController"), ("h6/users.js", 2, "Method", "BaseController.list"),
            ("h6/users.js", 6, "Class", "UsersController"), ("h6/users.js", 7, "Method", "UsersController.constructor"),
            ("h6b/users.js", 1, "Class", "BaseController"), ("h6b/users.js", 2, "Method", "BaseController.list"),
            ("h6b/users.js", 6, "Class", "UsersController"), ("h6b/users.js", 7, "Method", "UsersController.constructor"),
            ("h8/users.js", 1, "Class", "Base"), ("h8/users.js", 2, "Method", "Base.list"),
            ("h8/users.js", 4, "Class", "UsersController"), ("h8b/users.js", 1, "Class", "Base"),
            ("h8b/users.js", 2, "Method", "Base.list"), ("h8b/users.js", 4, "Class", "UsersController"),
            ("h26/users.js", 1, "Function", "renderList"), ("h26/users.js", 2, "Class", "Base"),
            ("h26/users.js", 3, "Method", "Base.list"), ("h26/users.js", 5, "Class", "UsersController"),
            ("h28/users.js", 1, "Class", "BaseController"), ("h28/users.js", 2, "Method", "BaseController.list"),
            ("h28/users.js", 6, "Function", "listUsers"), ("h28/users.js", 7, "Class", "UsersController"),
            ("h28/users.js", 8, "Method", "UsersController.constructor"), ("h31/users.js", 1, "Class", "CrudController"),
            ("h31/users.js", 2, "Method", "CrudController.constructor"), ("h31/users.js", 7, "Method", "CrudController.list"),
            ("h31/users.js", 10, "Method", "CrudController.show"), ("h31/users.js", 14, "Class", "UsersController"),
            ("h31/users.js", 15, "Method", "UsersController.constructor"))]
    # 0.8.51 also node-ifies the members a constructor or a prototype assignment defines
    late = [("UsersController.list", "src/h6/users.js", "Method", 9), ("UsersController.list", "src/h8b/users.js", "Method", 5),
            ("UsersController.show", "src/h31/users.js", "Method", 17)]
    got = {ver: _route_rows(ed, nodes + extra) for ver, extra in (("0.8.39", []), ("0.8.51", late))}
    kept = [(("BaseController.list", "src/h6b/users.js", 2), "INFERRED", "/h6b", ()),
            (("CrudController.list", "src/h31/users.js", 7), "INFERRED", "/h31", ())]
    check("a member the class sets itself, outside its body's methods, is its own: a `this.show = async ...` / `this.list "
          "= listUsers` its constructor writes (h31: a CRUD base whose constructor self-binds its methods), "
          "`UsersController.list = ...` / `UsersController.prototype.list = ...` after the class — the route binds that "
          "member (0.8.51's node) or nothing, never the base's method; the self-bind `this.list = this.list.bind(this)` "
          "(in the subclass's constructor or the base's) keeps the inherited method",
          got == {"0.8.39": sorted(kept),
                  "0.8.51": sorted(kept + [(("UsersController.list", "src/h6/users.js", 9), "INFERRED", "/h6", ()),
                                           (("UsersController.list", "src/h8b/users.js", 5), "INFERRED", "/h8b", ()),
                                           (("UsersController.show", "src/h31/users.js", 17), "INFERRED", "/h31/:id", ())])},
          got)
    rebound = {"src/h6c/users.js": ("class BaseController {\n  list(req, res) {\n    res.json([]);\n  }\n  show(req, res) {\n"
                                    "    res.json({});\n  }\n}\nclass UsersController extends BaseController {\n  constructor() {\n"
                                    "    super();\n    this.show = this.list.bind(this);\n  }\n}\nmodule.exports = new UsersController();\n"),
               "src/routes.js": (cjs_head + "const h6c = require('./h6c/users');\nrouter.get('/h6c', h6c.list);\n"
                                 "router.get('/h6c/:id', h6c.show);\nmodule.exports = router;\n")}
    ed, _rows = _tree(base, "member_rebound", rebound)
    got = _route_rows(ed, [("routes", "src/routes.js", "Module", 1), ("BaseController", "src/h6c/users.js", "Class", 1),
                           ("BaseController.list", "src/h6c/users.js", "Method", 2),
                           ("BaseController.show", "src/h6c/users.js", "Method", 5),
                           ("UsersController", "src/h6c/users.js", "Class", 9),
                           ("UsersController.constructor", "src/h6c/users.js", "Method", 10)])
    check("...while `this.show = this.list.bind(this)` (another method bound under this name) makes `show` the class's own: "
          "/h6c/:id binds nothing (no engine node there), never the base's BaseController.show; /h6c keeps the inherited "
          "BaseController.list",
          got == [(("BaseController.list", "src/h6c/users.js", 2), "INFERRED", "/h6c", ())], got)
    # a member read on a NAMED import's class or on the router file's own `new X()` of it ("pref:" placement)
    pref = {"src/p1/base.ts": "export class BaseController {\n  list(req: any, res: any) {}\n}\n",
            "src/p1/users.controller.ts": ("import { BaseController } from './base';\n"
                                           "export class UsersController extends BaseController {\n"
                                           "  static index(req: any, res: any) {}\n}\n"),
            "src/p2/users.controller.ts": ("class Base {\n  static list(req: any, res: any) {}\n}\n"
                                           "export class UsersController extends Base {}\n"),
            "src/routes.ts": (ts_head + "import { UsersController } from './p1/users.controller';\n"
                              "import { UsersController as U2 } from './p2/users.controller';\n"
                              "function list(req: any, res: any) {}\nconst router = Router();\n"
                              "const users = new UsersController();\nrouter.get('/p1', users.list);\n"
                              "router.get('/p2', U2.list);\nrouter.get('/p0', list);\nexport default router;\n")}
    ed, _rows = _tree(base, "member_named_import", pref)
    got = _route_rows(ed, [("routes", "src/routes.ts", "Module", 1), ("list", "src/routes.ts", "Function", 4),
                           ("BaseController", "src/p1/base.ts", "Class", 1),
                           ("BaseController.list", "src/p1/base.ts", "Method", 2),
                           ("UsersController", "src/p1/users.controller.ts", "Class", 2),
                           ("UsersController.index", "src/p1/users.controller.ts", "Method", 3),
                           ("Base", "src/p2/users.controller.ts", "Class", 1),
                           ("Base.list", "src/p2/users.controller.ts", "Method", 2),
                           ("UsersController", "src/p2/users.controller.ts", "Class", 4)])
    check("a member read on an instance the router file makes of a NAMED import (`const users = new UsersController(); "
          "users.list`) or on that class itself (`U2.list`, renamed) binds what its `extends` chain gives: the imported "
          "base's BaseController.list, the local base's static Base.list — never the router file's own `function list` "
          "(whose /p0 row stays EXTRACTED)",
          got == [(("Base.list", "src/p2/users.controller.ts", 2), "INFERRED", "/p2", ()),
                  (("BaseController.list", "src/p1/base.ts", 2), "INFERRED", "/p1", ()),
                  (("list", "src/routes.ts", 4), "EXTRACTED", "/p0", ())], got)
    expr_base = {"src/p3/users.controller.ts": ("const Base = class {\n  list(req: any, res: any) {}\n};\n"
                                                "export class UsersController extends Base {}\n"),
                 "src/p4/users.controller.ts": ("function list(items: any) {\n  return items;\n}\nconst Base = class {\n"
                                                "  list(req: any, res: any) {}\n};\nexport class UsersController extends Base {}\n"),
                 "src/routes.ts": (ts_head + "import { UsersController as U3 } from './p3/users.controller';\n"
                                   "import { UsersController as U4 } from './p4/users.controller';\nconst router = Router();\n"
                                   "const u3 = new U3();\nconst u4 = new U4();\nrouter.get('/p3', u3.list);\n"
                                   "router.get('/p4', u4.list);\nexport default router;\n")}
    ed, _rows = _tree(base, "member_named_import_expr", expr_base)
    # the engines name a class expression's method bare, one node per name in a file (the first: p4's helper)
    got = _route_rows(ed, [("routes", "src/routes.ts", "Module", 1), ("list", "src/p3/users.controller.ts", "Function", 2),
                           ("UsersController", "src/p3/users.controller.ts", "Class", 4),
                           ("list", "src/p4/users.controller.ts", "Function", 1),
                           ("UsersController", "src/p4/users.controller.ts", "Class", 7)])
    check("...and through a named import whose class extends a class-expression const (`const Base = class { list() {} "
          "}`): /p3 binds the expression's bare `list` at its line; where the module also declares a `function list`, the "
          "line names the method, so /p4 binds nothing — never that helper",
          got == [(("list", "src/p3/users.controller.ts", 2), "INFERRED", "/p3", ())], got)


# =====================================================================================================
# P — persistence, registry discipline, seam
# =====================================================================================================
class _Sess:
    def __init__(self):
        self.log: list = []

    def run(self, cypher, **params):
        for row in params.get("rows") or [params]:
            self.log.append((cypher, row))


def _persisted(log, et="CASCADES") -> set:
    out = set()
    for c, p in log:
        if "MERGE" in c and f"[r:{et}]" in c:
            out.add((p["b"], p["a"]) if f"MERGE (b)-[r:{et}]->(a)" in c else (p["a"], p["b"]))
    return out


def gate_cascade_persisted() -> None:
    print("\n-- P1: CASCADES persist REVERSED: impact(Owner) lists Cat, impact(Cat) lists Toy --")
    if T is None:
        check("build/typescript_edges.py exists (the recognizer)", False, "ImportError")
        return
    nodes = [{"key": f"{f}::{n}:5", "label": "Class", "name": n, "source_file": f}
             for n, f in (("Cat", "src/cat.entity.ts"), ("Owner", "src/owner.entity.ts"),
                          ("Toy", "src/toy.entity.ts"), ("Profile", "src/profile.entity.ts"))]
    keys = {n["name"]: n["key"] for n in nodes}
    src = APP["nest/src/cats/cat.entity.ts"].replace("../owners/", "./").replace("../common/", "./")
    _n, edges = T.recognize(src, "src/cat.entity.ts")
    s = _Sess()
    ER.push_typed_edges(s, edges, ER.Resolver(nodes), [n["key"] for n in nodes])
    graph = _persisted(s.log)
    check("persisted (Cat)-[:CASCADES]->(Owner) (onDelete on Cat's foreign key) and (Toy)-[:CASCADES]->(Cat)",
          {(keys["Cat"], keys["Owner"]), (keys["Toy"], keys["Cat"]), (keys["Cat"], keys["Profile"])} == graph,
          str(sorted(graph)))

    def impact(k):
        return {a for a, b in graph if b == k}
    check("impact('Owner') lists Cat; impact('Cat') lists Toy, not Owner",
          keys["Cat"] in impact(keys["Owner"]) and keys["Owner"] not in impact(keys["Cat"])
          and keys["Toy"] in impact(keys["Cat"]))


def gate_module_scope_resolution() -> None:
    print("\n-- P4: edge_resolve's TypeScript module scope (a member of an imported module object) --")
    if T is None:
        check("build/typescript_edges.py exists (the recognizer)", False, "ImportError")
        return

    def node(name, sf, label="Function"):
        return {"key": f"{sf}::{name}", "label": label, "name": name, "source_file": sf}

    nodes = [node("routes", "api/routes.js", "Module"),
             node("remove", "api/controllers/users.js"),
             node("CatsController.remove", "nest/cats.controller.ts", "Method"),
             node("UserController.list", "api/controllers/user.controller.ts", "Method"),
             node("UserController", "api/controllers/user.controller.ts", "Class"),
             node("show", "api/controllers/orders/index.ts"),
             node("list", "api/controllers/mixed.ts"), node("Mixed.list", "api/controllers/mixed.ts", "Method"),
             node("load", "src/features/a/loader.ts"), node("load", "apps/b/src/features/a/loader.ts"),
             node("load", "src/features/a/loader2.ts"), node("load", "src/xfeatures/a/loader.ts")]
    res = ER.Resolver(nodes)
    live = [n["key"] for n in nodes]

    def rows(dst, scope):
        e = FE.TypedEdge("routes", "api/routes.js", dst, "ROUTES_TO", "NAME", {}, scope)
        return sorted((r[2], r[3]) for r in ER.resolve_typed_edges([e], res, live))

    got = rows("remove", T.module_scope("api/routes.js", "./controllers/users"))
    check("module:<path> binds the name defined in THAT module, INFERRED — never CatsController.remove",
          got == [("api/controllers/users.js::remove", "INFERRED")], got)
    got = rows("list", T.module_scope("api/routes.js", "./controllers/user.controller"))
    check("...a module whose default export is a class instance: the same-named method of its class",
          got == [("api/controllers/user.controller.ts::UserController.list", "INFERRED")], got)
    got = rows("list", T.module_scope("api/routes.js", "./controllers/mixed"))
    check("...an exact top-level name wins over a same-named method of the module",
          got == [("api/controllers/mixed.ts::list", "INFERRED")], got)
    got = rows("show", T.module_scope("api/routes.js", "./controllers/orders"))
    check("...a directory import names its index file", got == [("api/controllers/orders/index.ts::show",
                                                                  "INFERRED")], got)
    got = rows("missing", T.module_scope("api/routes.js", "./controllers/users"))
    check("...a name the module does not define resolves to nothing (never a repo-wide guess)", got == [], got)
    got = rows("load", T.module_scope("src/x.ts", "@/features/a/loader"))
    check("modsuffix:<path> (an alias import) matches path-segment-aligned suffixes only; two -> AMBIGUOUS",
          got == [("apps/b/src/features/a/loader.ts::load", "AMBIGUOUS"),
                  ("src/features/a/loader.ts::load", "AMBIGUOUS")], got)
    check("module_scope: a relative import -> module:<root-relative path>; an alias -> modsuffix:; a workspace "
          "package or a path above the root -> no scope",
          (T.module_scope("a/b/c.js", "../d/e.js"), T.module_scope("a.ts", "~/x/y"), T.module_scope("a.ts", "#lib/z"),
           T.module_scope("a.ts", "@acme/pkg"), T.module_scope("a.ts", "../up"))
          == ("module:a/d/e", "modsuffix:x/y", "modsuffix:lib/z", "", ""))
    go_edge = FE.TypedEdge("routes", "api/routes.js", "remove", "ROUTES_TO", "NAME", {}, "nosuchkind:x")
    check("control: an unknown scope kind still resolves to nothing",
          ER.resolve_typed_edges([go_edge], res, live) == [])

    # "pref:<module scope>": a TYPE reference / an imported name prefers the module its import names; when
    # that module defines nothing of the name (a barrel re-export) the plain global rule applies unchanged.
    tnodes = [node("CatsController", "app1/cats.controller.ts", "Class"),
              node("CatsService", "app1/cats.service.ts", "Class"), node("CatsService", "app2/cats.service.ts", "Class"),
              node("CatsService", "app3/cats.service.ts", "Class"),
              node("Cat", "app1/cat.entity.ts", "Class"), node("Owner", "app1/owner.entity.ts", "Class"),
              node("Owner", "legacy/owner.ts", "Class"), node("Address", "app1/owners/address.entity.ts", "Class"),
              node("index", "app1/owners/index.ts", "Module")]
    tres = ER.Resolver(tnodes)
    tlive = [n["key"] for n in tnodes]

    def trows(edge):
        return sorted((r[0], r[1], r[2], r[3]) for r in ER.resolve_typed_edges([edge], tres, tlive))

    dep = FE.TypedEdge("CatsController", "app1/cats.controller.ts", "CatsService", "DEPENDS_ON", "NAME", {},
                       "pref:" + T.module_scope("app1/cats.controller.ts", "./cats.service"))
    check("pref: a constructor dependency binds the CatsService its import names, INFERRED — not the two other "
          "apps' CatsService (unscoped, that is a 3-way AMBIGUOUS fan-out)",
          trows(dep) == [("DEPENDS_ON", "app1/cats.controller.ts::CatsController", "app1/cats.service.ts::CatsService",
                          "INFERRED")]
          and len(trows(dep._replace(scope=""))) == 3, (trows(dep), trows(dep._replace(scope=""))))
    bt = FE.TypedEdge("Owner", "app1/cat.entity.ts", "Cat", "CASCADES", "NAME", {},
                      "pref:" + T.module_scope("app1/cat.entity.ts", "./owner.entity"))
    check("pref: a belongs-to cascade's principal (the SRC, from another file) binds the imported Owner — unscoped "
          "it is dropped (two :Class Owner)",
          trows(bt) == [("CASCADES", "app1/owner.entity.ts::Owner", "app1/cat.entity.ts::Cat", "INFERRED")]
          and trows(bt._replace(scope="")) == [], (trows(bt), trows(bt._replace(scope=""))))
    barrel = FE.TypedEdge("Owner", "app1/owner.entity.ts", "Address", "REFERENCES", "NAME", {},
                          "pref:" + T.module_scope("app1/owner.entity.ts", "./owners"))
    check("pref: through a barrel (the module defines nothing of the name) the global rule applies unchanged",
          trows(barrel) == trows(barrel._replace(scope="")) == [
              ("REFERENCES", "app1/owner.entity.ts::Owner", "app1/owners/address.entity.ts::Address", "INFERRED")],
          (trows(barrel), trows(barrel._replace(scope=""))))
    check("control: a Go package scope is untouched by the TypeScript scopes (an unknown Go package -> nothing)",
          trows(dep._replace(scope="import:example.com/none")) == [])

    print("\n-- P5: edge_resolve's TypeScript scopes after finalize: exact, unplaced imports, guessed aliases --")
    gnodes = tnodes + [node("LoggerService", "libs/common/src/logger.service.ts", "Class"),
                       node("LoggerService", "apps/admin/src/common.ts", "Class"),
                       node("routes", "api/routes.js", "Module"),
                       node("show", "api/controllers/users.js"), node("show", "api/controllers/orders.js")]
    gres = ER.Resolver(gnodes)
    glive = [n["key"] for n in gnodes]

    def grows(edge):
        return sorted((r[1], r[2], r[3]) for r in ER.resolve_typed_edges([edge], gres, glive))

    ex = dep._replace(scope="exact:module:app1/cats.service")
    check("exact:module:<p> (finalize found the definition there) binds that module's symbol",
          grows(ex) == [("app1/cats.controller.ts::CatsController", "app1/cats.service.ts::CatsService", "INFERRED")],
          grows(ex))
    check("...and nothing when that module has no node of the name (never the global rule's other CatsService)",
          grows(ex._replace(scope="exact:module:app1/other")) == [], grows(ex._replace(scope="exact:module:app1/other")))
    log = FE.TypedEdge("UsersService", "app1/cats.controller.ts", "LoggerService", "DEPENDS_ON", "NAME", {},
                       "pref:modsuffix:common")
    check("a guessed alias (pref:modsuffix:) never narrows two candidates to the one whose path ends with it: both "
          "AMBIGUOUS", [r[1] for r in grows(log._replace(src_name="CatsController"))]
          == ["apps/admin/src/common.ts::LoggerService", "libs/common/src/logger.service.ts::LoggerService"]
          and {r[2] for r in grows(log._replace(src_name="CatsController"))} == {"AMBIGUOUS"},
          grows(log._replace(src_name="CatsController")))
    check("an import finalize could not place in the tree (pref:spec:<specifier>) binds nothing",
          grows(dep._replace(scope="pref:spec:@vuex-orm/core")) == [], grows(dep._replace(scope="pref:spec:@vuex-orm/core")))
    mo = FE.TypedEdge("routes", "api/routes.js", "show", "ROUTES_TO", "NAME", {}, "module:api/controllers/users#*")
    check("a module-object scope's `#` note (what finalize follows) does not change its module: module:<p>#x binds "
          "inside <p>", [r[1] for r in grows(mo)] == ["api/controllers/users.js::show"], grows(mo))
    noted = dep._replace(scope="pref:module:app1/cats.service#default")
    check("...nor does a preferred import's (`pref:module:<p>#default`, finalize not run): <p>'s own symbol binds",
          grows(noted) == [("app1/cats.controller.ts::CatsController", "app1/cats.service.ts::CatsService", "INFERRED")],
          grows(noted))


def gate_registry_entry() -> None:
    print("\n-- P2: the registry entry (drift-locked; retrieval's default walk unchanged) --")
    entry = next((r for r in LR.REGISTRY if r.name == "typescript"), None)
    check("language_recognizers.REGISTRY has a 'typescript' entry", entry is not None,
          [r.name for r in LR.REGISTRY])
    if entry is None:
        return
    check("it covers .ts .tsx .js .jsx .mjs .cjs (JavaScript too)",
          set(entry.extensions) == {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}, entry.extensions)
    check("its vocabulary uses only existing edge types (⊆ ALLOWED_EDGE_TYPES; no new type)",
          set(entry.edge_types) <= set(FE.ALLOWED_EDGE_TYPES) and len(FE.ALLOWED_EDGE_TYPES) == 13,
          set(entry.edge_types) - set(FE.ALLOWED_EDGE_TYPES))
    spec = importlib.util.spec_from_file_location("dmts_retrieval", BUILD / "retrieval.py")
    R = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(R)
    check("its traversable types are all ALREADY in retrieval.DEFAULT_IMPACT_EDGE_TYPES (retrieval.py untouched)",
          set(entry.impact_edge_types) <= set(R.DEFAULT_IMPACT_EDGE_TYPES), entry.impact_edge_types)
    check("HAS_MANY / REFERENCES / IMPLEMENTS stay out of the default walk (benchmark-comparability invariant)",
          not ({"HAS_MANY", "REFERENCES", "IMPLEMENTS"} & set(entry.impact_edge_types)))
    check("the cross-file post-pass is wired (finalize declares the caller's `exclude`)",
          entry.finalize is not None and "exclude" in entry.finalize.__code__.co_varnames)


def gate_seam_and_vendor(base: Path) -> None:
    print("\n-- P3: the sync seam: vendored / build output never reaches the reader; DEEPMAP_RECOGNIZERS --")
    if T is None:
        check("build/typescript_edges.py exists (the recognizer)", False, "ImportError")
        return
    root = base / "seam"
    ctrl = APP["nest/src/cats/cats.controller.ts"]
    files = {"src/cats.controller.ts": ctrl, "node_modules/pkg/cats.controller.ts": ctrl,
             "dist/cats.controller.js": ctrl, "build/cats.controller.js": ctrl, ".next/server/x.js": ctrl,
             "src/cats.d.ts": ctrl, "public/vendor.min.js": ctrl, "coverage/lcov/x.ts": ctrl}
    _write_app(root, files)
    with _env(**_FLAGS), contextlib.redirect_stderr(io.StringIO()):
        merged, typed = S._apply_recognizers(root, [])
    srcs = sorted({e.src_file for e in typed})
    check("only src/cats.controller.ts contributes (node_modules/, dist/, build/, .next/, coverage/, .d.ts, "
          "minified excluded)", srcs == ["src/cats.controller.ts"], srcs)
    check("...and only its ensured node is merged", sorted(n["source_file"] for n in merged)
          == ["src/cats.controller.ts"], [n["source_file"] for n in merged])
    with _env(**dict(_FLAGS, DEEPMAP_RECOGNIZERS="0")):
        check("DEEPMAP_RECOGNIZERS=0 -> the layer is off", LR.run(root) == ([], []))
    with _env(**dict(_FLAGS, DEEPMAP_RECOGNIZERS="typescript")):
        supp, edges = LR.run(root)
        check("DEEPMAP_RECOGNIZERS=typescript runs this entry alone", len(edges) >= 5 and supp, (len(edges), supp))
    with _env(**dict(_FLAGS, DEEPMAP_RECOGNIZERS="python")):
        check("DEEPMAP_RECOGNIZERS=python leaves TypeScript out", LR.run(root)[1] == [])


def gate_walk_and_ignores(base: Path) -> None:
    print("\n-- P6: the walk: feature modules under log/ build/ target/ are read; node_modules/ is never descended --")
    if T is None:
        check("build/typescript_edges.py exists (the recognizer)", False, "ImportError")
        return
    root = base / "walk"
    ctrl = ("import {{ Controller, Get }} from '@nestjs/common';\n\n@Controller('{p}')\nexport class {c} {{\n"
            "  @Get()\n  all() {{\n    return [];\n  }}\n}}\n")
    files = {f"src/{d}/{d}.controller.ts": ctrl.format(p=d, c=d.title() + "Controller")
             for d in ("users", "log", "build", "target", "tmp", "bin", "obj")}
    files.update({"packages/a/package.json": '{ "name": "a" }\n',
                  "packages/a/src/build/jobs.controller.ts": ctrl.format(p="jobs", c="JobsController"),
                  "packages/a/build/compiled.controller.ts": ctrl.format(p="x", c="XController"),
                  "build/out.controller.ts": ctrl.format(p="y", c="YController"),
                  "out/web.controller.ts": ctrl.format(p="z", c="ZController")})
    _write_app(root, files)
    with _env(**_FLAGS), contextlib.redirect_stderr(io.StringIO()):
        _merged, typed = S._apply_recognizers(root, [])
    got = sorted({e.src_file for e in typed})
    check("NestJS feature modules named log / build / target / tmp / bin / obj below src/ are read (the engine "
          "extracts them); build/ and out/ directly in the root or in a package root (a package.json beside "
          "them) are output and stay unread",
          got == ["packages/a/src/build/jobs.controller.ts"] + [f"src/{d}/{d}.controller.ts" for d in
                                                                 ("bin", "build", "log", "obj", "target", "tmp",
                                                                  "users")], got)
    nm = base / "walk_nm"
    _write_app(nm, {"src/app.ts": ("import express from 'express';\nimport { list } from './h';\nconst app = express();\n"
                                   "app.get('/x', list);\n"),
                    "src/h.ts": "export function list() {}\n"})
    for i in range(30):
        _write_app(nm, {f"node_modules/pkg{i}/lib/m.js": "module.exports = 1;\n",
                        f"node_modules/pkg{i}/lib/n.ts": "export const n = 1;\n"})
    seen: list = []
    real = Path.is_file

    def spy(self, *a, **kw):
        seen.append(self.as_posix())
        return real(self, *a, **kw)
    Path.is_file = spy
    try:
        with _env(**dict(_FLAGS, DEEPMAP_RECOGNIZERS="typescript")), contextlib.redirect_stderr(io.StringIO()):
            _supp, edges = LR.run(nm)
    finally:
        Path.is_file = real
    check("the TypeScript walk never stats a file under node_modules/ (it is pruned, not filtered per file)",
          not [p for p in seen if "/node_modules/" in p], len([p for p in seen if "/node_modules/" in p]))
    check("...and finalize reuses the registry walk's file list (src/app.ts is looked at once, not twice)",
          sum(p.endswith("/src/app.ts") for p in seen) == 1 and [x.dst_name for x in _e(edges, "ROUTES_TO")] == ["list"],
          (sum(p.endswith("/src/app.ts") for p in seen), edges))


# =====================================================================================================
# S — linear cost
# =====================================================================================================
def _work(src: str, rel: str) -> int:
    """Lines of typescript_edges.py executed while recognizing `src` (deterministic; no clock)."""
    mine, count = T.recognize.__code__.co_filename, 0

    def local(frame, event, arg):
        nonlocal count
        if event == "line":
            count += 1
        return local

    def enter(frame, event, arg):
        return local if frame.f_code.co_filename == mine else None

    prev = sys.gettrace()
    sys.settrace(enter)
    try:
        T.recognize(src, rel)
    finally:
        sys.settrace(prev)
    return count


def _scale_controllers(n: int) -> str:
    out = ["import { Controller, Get, Post, Injectable } from '@nestjs/common';"]
    for i in range(n):
        out += [f"@Controller('c{i}')", f"export class C{i}Controller {{",
                f"  constructor(private readonly s: S{i}Service) {{}}",
                "  @Get(':id')", "  one(id: string) { return this.s.get(id); }",
                "  @Post()", "  make() { return this.s.make({ a: [1, 2, 3], b: `t${1}` }); }", "}"]
    return "\n".join(out)


def _scale_router(n: int) -> str:
    out = ["const express = require('express');", "const router = express.Router();"]
    for i in range(n):
        out += [f"function h{i}(req, res) {{ res.json({{ i: {i}, s: 'a/b' }}); }}",
                f"router.get('/r{i}/:id', h{i});", f"router.route('/c{i}').get(h{i}).post(h{i});",
                f"const m{i} = new Map(); m{i}.get('/x{i}');"]
    return "\n".join(out)


def _scale_entities(n: int) -> str:
    out = ["import { Entity, ManyToOne, OneToMany } from 'typeorm';"]
    for i in range(n):
        out += ["@Entity()", f"export class E{i} {{",
                f"  @ManyToOne(() => P{i}, (p) => p.items, {{ onDelete: 'CASCADE' }})", f"  parent: P{i};",
                f"  @OneToMany(() => K{i}, (k) => k.owner, {{ cascade: true }})", f"  kids: K{i}[];", "}"]
    return "\n".join(out)


def _scale_class_routes(n: int) -> str:
    out = ["import { Router } from 'express';", "import { Svc } from './svc';", "const API = '/api';"]
    for i in range(n):
        out += [f"export class R{i} {{", f"  public path = '/r{i}';", "  public router: Router = Router();",
                f"  private readonly c = new C{i}();",
                "  constructor(private readonly s: Svc, private readonly api: Router) {",
                "    this.router.get(`${API}${this.path}/:id`, this.c.one);",
                "    this.api.post(this.path, this.c.make);", "  }",
                "  h = async (req, res) => res.json({});", "}"]
    return "\n".join(out)


def _scale_plugins(n: int) -> str:
    """Many route-registering functions over the same conventional receiver name (`function regN(app)`), with
    no module-level `app`: every route call looks up the plugin function around it."""
    out = ["import express from 'express';"]
    for i in range(n):
        out += [f"function h{i}(req, res) {{ res.json({{}}); }}", f"export function reg{i}(app) {{",
                f"  app.get('/r{i}', h{i});", f"  app.post('/r{i}', h{i});", "}"]
    return "\n".join(out)


def _scale_noise(n: int) -> str:
    """No framework at all: strings, templates, regexes, comments, deep nesting."""
    out = []
    for i in range(n):
        out += [f"const s{i} = 'it\\'s /{i}/ // not a comment';", f"const t{i} = `a${{ {{ x: `b${{i}}` }} }}c`;",
                f"const r{i} = /a\\/b[/]{i}/g.test('x') ? 1 / 2 : 3;", f"/* block {i} */ function f{i}(a) {{ "
                f"return [a, {{ b: (a) => ({{ c: a }}) }}]; }}"]
    return "\n".join(out)


class _Scanned(str):
    """A source text that counts the characters its own str methods scan (slices, find, rfind): the lexer's work
    outside the regex engine, which a count of lines run cannot see."""
    chars = 0

    def __getitem__(self, k):
        if isinstance(k, slice):
            a, b, _step = k.indices(len(self))
            _Scanned.chars += max(0, b - a)
        return str.__getitem__(self, k)

    def find(self, sub, start=None, end=None):
        r = str.find(self, sub, start, end)
        a, b = start or 0, len(self) if end is None else end
        _Scanned.chars += (r + len(sub) - a) if r >= 0 else max(0, b - a)
        return r

    def rfind(self, sub, start=None, end=None):
        r = str.rfind(self, sub, start, end)
        a, b = start or 0, len(self) if end is None else end
        _Scanned.chars += (b - r) if r >= 0 else max(0, b - a)
        return r


def _lex_scanned(text: str) -> int:
    _Scanned.chars = 0
    T._lex(_Scanned(text), jsx=True)
    return _Scanned.chars


def _scale_class_chain(n: int) -> str:
    """A class expression, then n - 1 consts each a class expression extending the one before, the last one's instance
    exported: every hop names another local const (finalize's shape fixpoint, _ModFacts)."""
    out = ["const C0 = class {", "  list(req, res) {}", "};"]
    out += [f"const C{i} = class extends C{i - 1} {{}};" for i in range(1, n)]
    return "\n".join(out + [f"module.exports = new C{n - 1}();", ""])


def _scale_subclasses(n: int) -> str:
    """n classes extending one base, each setting members on itself (`this.x = ...`, a self-bind, `Ci.prototype.y =
    ...`): the members a class holds outside its body's methods (_ModFacts.heritage)."""
    out = ["class Base {", "  list(req, res) {}", "}"]
    for i in range(n):
        out += [f"class C{i} extends Base {{", "  constructor() {", "    super();", f"    this.x{i} = 1;",
                "    this.list = this.list.bind(this);", "  }", f"  m{i}(req, res) {{}}", "}",
                f"C{i}.prototype.y{i} = function () {{}};"]
    return "\n".join(out + ["module.exports = new C0();", ""])


class _RxCount:
    """A compiled pattern of typescript_edges.py that counts the characters it is handed to scan: a search or a
    finditer up to where it stops, a match its own length (1 when it fails), a sub / split / findall all of it."""
    chars = 0

    def __init__(self, pat):
        self._pat = pat

    def __getattr__(self, name):
        return getattr(self._pat, name)

    def _anchored(self, how, s, pos, endpos):
        m = how(s, pos, endpos)
        _RxCount.chars += (m.end() - pos) if m else 1
        return m

    def match(self, s, pos=0, endpos=sys.maxsize):
        return self._anchored(self._pat.match, s, pos, endpos)

    def fullmatch(self, s, pos=0, endpos=sys.maxsize):
        return self._anchored(self._pat.fullmatch, s, pos, endpos)

    def search(self, s, pos=0, endpos=sys.maxsize):
        m = self._pat.search(s, pos, endpos)
        _RxCount.chars += max(0, (m.end() if m else min(endpos, len(s))) - pos)
        return m

    def finditer(self, s, pos=0, endpos=sys.maxsize):
        last = pos
        for m in self._pat.finditer(s, pos, endpos):
            _RxCount.chars += max(0, m.end() - last)
            last = m.end()
            yield m
        _RxCount.chars += max(0, min(endpos, len(s)) - last)

    def findall(self, s, pos=0, endpos=sys.maxsize):
        _RxCount.chars += max(0, min(endpos, len(s)) - pos)
        return self._pat.findall(s, pos, endpos)

    def sub(self, repl, s, count=0):
        _RxCount.chars += len(s)
        return self._pat.sub(repl, s, count)

    def split(self, s, maxsplit=0):
        _RxCount.chars += len(s)
        return self._pat.split(s, maxsplit)


class _ReShim:
    """typescript_edges.py's `re` while a cost is counted: what it compiles, and its module-level calls, count."""

    def __init__(self, real):
        self._re = real

    def __getattr__(self, name):
        return getattr(self._re, name)

    def compile(self, pattern, flags=0):
        return pattern if isinstance(pattern, _RxCount) else _RxCount(self._re.compile(pattern, flags))

    def match(self, pattern, s, flags=0):
        return self.compile(pattern, flags).match(s)

    def fullmatch(self, pattern, s, flags=0):
        return self.compile(pattern, flags).fullmatch(s)

    def search(self, pattern, s, flags=0):
        return self.compile(pattern, flags).search(s)

    def finditer(self, pattern, s, flags=0):
        return self.compile(pattern, flags).finditer(s)

    def findall(self, pattern, s, flags=0):
        return self.compile(pattern, flags).findall(s)

    def sub(self, pattern, repl, s, count=0, flags=0):
        return self.compile(pattern, flags).sub(repl, s, count)

    def split(self, pattern, s, maxsplit=0, flags=0):
        return self.compile(pattern, flags).split(s, maxsplit)


@contextlib.contextmanager
def _counting_regexes():
    """Every compiled pattern typescript_edges.py holds at module level, and its `re`, count (_RxCount)."""
    pats = {k: v for k, v in vars(T).items() if isinstance(v, re.Pattern)}
    real = T.re
    try:
        for k, v in pats.items():
            setattr(T, k, _RxCount(v))
        T.re = _ReShim(real)
        yield
    finally:
        for k, v in pats.items():
            setattr(T, k, v)
        T.re = real


def _mod_work(src: str, rel: str) -> int:
    """What finalize's facts of one module cost (_ModFacts over the lexed file): lines of typescript_edges.py run plus
    the characters its regexes are handed to scan — a whole-file regex per class runs few lines (deterministic)."""
    f = T._File(src, rel)
    mine, count = T.recognize.__code__.co_filename, 0

    def local(frame, event, arg):
        nonlocal count
        if event == "line":
            count += 1
        return local

    def enter(frame, event, arg):
        return local if frame.f_code.co_filename == mine else None

    _RxCount.chars = 0
    prev = sys.gettrace()
    with _counting_regexes():
        sys.settrace(enter)
        try:
            T._ModFacts(f)
        finally:
            sys.settrace(prev)
    return count + _RxCount.chars


def gate_linear_cost() -> None:
    print("\n-- S: the reader's work grows LINEARLY with the file (lines run, no clock) --")
    if T is None:
        check("build/typescript_edges.py exists (the recognizer)", False, "ImportError")
        return
    for desc, shape, n, rel in (("NestJS controllers", _scale_controllers, 25, "src/c.ts"),
                                ("an Express router with chains and Map.get look-alikes", _scale_router, 40, "r.js"),
                                ("TypeORM entities with cascades", _scale_entities, 25, "src/e.ts"),
                                ("class-based routers: typed fields, injected controllers, folded paths (every "
                                 "`this.` lookup finds its class)", _scale_class_routes, 50, "src/routes.ts"),
                                ("route-registering functions sharing a receiver name (`function regN(app)`)",
                                 _scale_plugins, 60, "src/plugins.ts"),
                                ("strings, templates, regexes, comments", _scale_noise, 40, "src/n.ts")):
        small, large = _work(shape(n), rel), _work(shape(4 * n), rel)
        ratio = large / max(small, 1)
        check(f"{desc}: 4x the input costs at most 5x the work (linear is ~4x)", ratio <= 5.0,
              f"lines run {small} -> {large} = x{ratio:.2f}")
    # the JSX-text rule looks at a bounded stretch around each backtick, never the whole line
    for desc, unit in (("one long line of templates holding JSX", "`m{i} <b>x</b>`, "),
                       ("one long line of JSX text with backticks", "<p>a{i} ` b</p> ")):
        def text(n):
            return "const a = 1;\n" * 50 + "export const msgs = [" + "".join(unit.format(i=i) for i in range(n)) + "];\n"
        small, large = _lex_scanned(text(500)), _lex_scanned(text(2000))
        ratio = large / max(small, 1)
        check(f"the lexer over {desc}: 4x the input scans at most 5x the characters (linear is ~4x)", ratio <= 5.0,
              f"chars scanned {small} -> {large} = x{ratio:.2f}")
    # finalize's facts of one module (_ModFacts): the shape fixpoint reads at most as many hops as _inherited follows,
    # and the members each class sets on itself come from one pass over the file
    for desc, shape, n in (("a chain of class-expression consts (`const C1 = class extends C0 {};` ... and "
                            "`module.exports = new C<n>()`): the shape fixpoint stops after _MAX_HERITAGE hops",
                            _scale_class_chain, 50),
                           ("classes extending a base, each setting members on itself (`this.x = ...`, a self-bind, "
                            "`Ci.prototype.y = ...`)", _scale_subclasses, 25)):
        small, large = _mod_work(shape(n), "src/users.js"), _mod_work(shape(4 * n), "src/users.js")
        ratio = large / max(small, 1)
        check(f"finalize's facts of a module of {desc}: 4x the input costs at most 5x the work (lines run + characters "
              f"its regexes scan; linear is ~4x)", ratio <= 5.0, f"work {small} -> {large} = x{ratio:.2f}")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 78)
    print(f"DeepMap — TypeScript / JavaScript framework recognizer gates (plugin #6; build: {BUILD})")
    print("=" * 78)
    base = Path(tempfile.mkdtemp(prefix="dmts_gates_"))
    try:
        for gate in (gate_registry_entry, gate_recognizer_core, gate_skips_and_shapes, gate_class_routers,
                     gate_reader_precision, gate_cascade_persisted, gate_module_scope_resolution, gate_linear_cost):
            try:
                gate()
            except Exception as exc:  # noqa: BLE001 — a crash on a pre-feature tree is a RED result
                import traceback
                traceback.print_exc()
                check(f"{gate.__name__} ran without raising", False, f"{type(exc).__name__}: {exc}")
        for gate in (gate_routes_mounts_paths, gate_import_resolution, gate_captured_engine, gate_live_engine,
                     gate_seam_and_vendor, gate_walk_and_ignores):
            try:
                gate(base)
            except Exception as exc:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check(f"{gate.__name__} ran without raising", False, f"{type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print("\n" + "=" * 78)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(capture() if _CAPTURE else main())
