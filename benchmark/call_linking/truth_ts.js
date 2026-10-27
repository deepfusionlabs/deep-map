// TypeScript / JavaScript call-site reference resolver (design §8.2): every CallExpression / NewExpression under
// <corpus>/<subdir>, resolved with the TypeScript 5.6.3 type checker (receiver types included). Same row shape
// as truth_jedi.py. MEASUREMENT ONLY: TypeScript lives in a scratch npm env, never in a shipped list.
//
//   npm install --prefix <scratch>/tsenv --save-exact typescript@5.6.3
//   node benchmark/call_linking/truth_ts.js <tsenv> <corpus> <subdir> <out.json> [--js] [--conditions=a,b]
//        [--tsconfig=<path>] [--any-ts]
//
//   <tsenv>       a directory holding node_modules/typescript (or the typescript package dir itself)
//   --js          also read .js/.jsx/.mjs/.cjs with allowJs + checkJs (a JavaScript corpus)
//   --conditions  package.json export conditions (zod: --conditions=@zod/source)
//   --tsconfig    merge that tsconfig's compilerOptions (paths, baseUrl, decorators ...) under the defaults
//   --any-ts      accept a TypeScript other than 5.6.3 (recorded in the output)
//
// form: bare | this (this.m()) | super | name_attr (x.m(): a namespace/module object OR a local) | recv
//       (a.b.m(), f().m() ...) | ctor (new X()) | other. defstack: [name, start line, [name line]] of each
// enclosing function-like with a name (a graph node may sit on the decorator / declarator line or on the
// name line). defs: in-corpus declarations, import aliases and CommonJS shorthand exports followed.
// Rows are sorted; the output carries no timing.
"use strict";
const path = require("path");
const fs = require("fs");

const WANT_TS = "5.6.3";
const argv = process.argv.slice(2);
const flags = argv.filter((a) => a.startsWith("--"));
const pos = argv.filter((a) => !a.startsWith("--"));
if (pos.length !== 4) {
  console.error("usage: node truth_ts.js <tsenv> <corpus> <subdir> <out.json> [--js] [--conditions=a,b] [--tsconfig=p] [--any-ts]");
  process.exit(2);
}
const flag = (name) => flags.find((f) => f === name || f.startsWith(name + "="));
const flagVal = (name) => { const f = flag(name); return f && f.includes("=") ? f.slice(f.indexOf("=") + 1) : null; };
const tsenv = path.resolve(pos[0]);
const tsPath = fs.existsSync(path.join(tsenv, "node_modules", "typescript")) ? path.join(tsenv, "node_modules", "typescript") : tsenv;
const ts = require(tsPath);
if (ts.version !== WANT_TS && !flag("--any-ts")) {
  console.error(`[truth_ts] REFUSED: TypeScript ${ts.version} (the registered reference is ${WANT_TS}; --any-ts to override)`);
  process.exit(2);
}
const root = path.resolve(pos[1]);
const sub = pos[2];
const out = pos[3];
const withJs = !!flag("--js");
const conditions = (flagVal("--conditions") || "").split(",").filter(Boolean);
const tsconfig = flagVal("--tsconfig");
const SKIP = new Set(["node_modules", "dist", "build", "coverage", "out", "graphify-out"]);
const EXT = withJs ? /\.(ts|tsx|mts|cts|js|jsx|mjs|cjs)$/ : /\.(ts|tsx|mts|cts)$/;

function walk(dir, acc) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0))) {
    if (e.name.startsWith(".") || SKIP.has(e.name)) continue;
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, acc);
    else if (EXT.test(e.name) && !/\.d\.[mc]?ts$/.test(e.name)) acc.push(p);
  }
  return acc;
}
const base = path.join(root, sub);
const files = walk(base, []);
let options = {
  target: ts.ScriptTarget.ESNext, module: ts.ModuleKind.NodeNext, moduleResolution: ts.ModuleResolutionKind.NodeNext,
  skipLibCheck: true, noEmit: true, strict: true, types: [], experimentalDecorators: true,
};
if (tsconfig) {
  const cfg = ts.readConfigFile(path.resolve(tsconfig), ts.sys.readFile);
  const parsed = ts.parseJsonConfigFileContent(cfg.config || {}, ts.sys, path.dirname(path.resolve(tsconfig)));
  options = Object.assign({}, options, parsed.options, { noEmit: true, skipLibCheck: true, types: [] });
}
if (conditions.length) options.customConditions = conditions;
if (withJs) Object.assign(options, { allowJs: true, checkJs: true, maxNodeModuleJsDepth: 0 });
const program = ts.createProgram(files, options);
const checker = program.getTypeChecker();
const rel = (f) => path.relative(root, f).split(path.sep).join("/");
const lineOf = (sf, p) => sf.getLineAndCharacterOfPosition(p).line + 1;
const inCorpus = (p) => p.startsWith(root + path.sep) && !p.includes(`${path.sep}node_modules${path.sep}`);

function fnName(n) {
  if ((ts.isFunctionDeclaration(n) || ts.isMethodDeclaration(n) || ts.isGetAccessor(n) || ts.isSetAccessor(n)) && n.name)
    return n.name.getText();
  if (ts.isConstructorDeclaration(n)) return "constructor";
  if ((ts.isArrowFunction(n) || ts.isFunctionExpression(n)) && n.parent) {
    const p = n.parent;
    if ((ts.isVariableDeclaration(p) || ts.isPropertyDeclaration(p) || ts.isPropertyAssignment(p)) && p.name)
      return p.name.getText();
  }
  return null;
}
function declNode(n) {   // the node whose start a graph node would carry
  if ((ts.isArrowFunction(n) || ts.isFunctionExpression(n)) && n.parent &&
      (ts.isVariableDeclaration(n.parent) || ts.isPropertyDeclaration(n.parent) || ts.isPropertyAssignment(n.parent)))
    return n.parent;
  return n;
}
function nameLine(sf, n) {
  const d = declNode(n);
  return d.name ? lineOf(sf, d.name.getStart(sf)) : null;
}
function lines(sf, n) {
  const start = lineOf(sf, declNode(n).getStart(sf));
  const nl = nameLine(sf, n);
  return [start, nl !== null && nl !== start ? [nl] : []];
}
function kindOf(d) {
  if (ts.isClassDeclaration(d) || ts.isClassExpression(d)) return "class";
  if (ts.isVariableDeclaration(d) && d.initializer && ts.isClassExpression(d.initializer)) return "class";
  if (ts.isFunctionLike(d)) return "function";
  if ((ts.isVariableDeclaration(d) || ts.isPropertyDeclaration(d) || ts.isPropertyAssignment(d)) && d.initializer &&
      ts.isFunctionLike(d.initializer)) return "function";
  return "statement";
}
function resolveDecls(sym, depth, acc) {
  if (!sym || depth > 4) return acc;
  if (sym.flags & ts.SymbolFlags.Alias) {
    try { sym = checker.getAliasedSymbol(sym); } catch (e) { /* keep the alias */ }
  }
  for (const d of sym.declarations || []) {
    if (ts.isShorthandPropertyAssignment(d)) {
      resolveDecls(checker.getShorthandAssignmentValueSymbol(d), depth + 1, acc);
    } else if (ts.isPropertyAssignment(d) && d.initializer && ts.isIdentifier(d.initializer)) {
      resolveDecls(checker.getSymbolAtLocation(d.initializer), depth + 1, acc);
    } else if (!acc.includes(d)) {
      acc.push(d);
    }
  }
  return acc;
}

const rows = [];
let nfiles = 0;
for (const sf of program.getSourceFiles()) {
  if (sf.isDeclarationFile) continue;
  const fp = path.resolve(sf.fileName);
  if (!fp.startsWith(base + path.sep) && fp !== base) continue;
  nfiles++;
  const stack = [];
  const visit = (node) => {
    const isFn = ts.isFunctionLike(node) && !ts.isFunctionTypeNode(node) && !ts.isMethodSignature(node) &&
      !ts.isCallSignatureDeclaration(node) && !ts.isConstructSignatureDeclaration(node) && !ts.isIndexSignatureDeclaration(node) &&
      !ts.isConstructorTypeNode(node);
    const name = isFn ? fnName(node) : null;
    if (name) { const [s, alt] = lines(sf, node); stack.push([name, s, alt]); }
    if (ts.isCallExpression(node) || ts.isNewExpression(node)) {
      const ex = node.expression;
      let form = "other", nameNode = null;
      if (ts.isNewExpression(node)) { form = "ctor"; nameNode = ts.isPropertyAccessExpression(ex) ? ex.name : (ts.isIdentifier(ex) ? ex : null); }
      else if (ts.isIdentifier(ex)) { form = "bare"; nameNode = ex; }
      else if (ts.isPropertyAccessExpression(ex)) {
        nameNode = ex.name;
        const r = ex.expression;
        if (r.kind === ts.SyntaxKind.ThisKeyword) form = "this";
        else if (r.kind === ts.SyntaxKind.SuperKeyword) form = "super";
        else if (ts.isIdentifier(r)) form = "name_attr";
        else form = "recv";
      } else if (ex.kind === ts.SyntaxKind.SuperKeyword) { form = "super"; }
      const at = node.getStart(sf);
      const row = { file: rel(fp), line: lineOf(sf, at), col: sf.getLineAndCharacterOfPosition(at).character, form,
                    name: nameNode ? nameNode.getText() : null, defstack: stack.map((e) => [e[0], e[1], e[2]]), defs: [] };
      if (nameNode) {
        const seen = new Set();
        for (const d of resolveDecls(checker.getSymbolAtLocation(nameNode), 0, [])) {
          const dsf = d.getSourceFile();
          const dp = path.resolve(dsf.fileName);
          if (!inCorpus(dp) || dsf.isDeclarationFile) continue;
          const start = lineOf(dsf, d.getStart(dsf));
          const nl = d.name ? lineOf(dsf, d.name.getStart(dsf)) : null;
          const kind = kindOf(d);
          const key = `${rel(dp)}:${start}:${kind}`;
          if (seen.has(key)) continue;
          seen.add(key);
          row.defs.push({ file: rel(dp), line: start, alt_lines: nl !== null && nl !== start ? [nl] : [], type: kind,
                          name: d.name ? d.name.getText() : row.name });
        }
        row.defs.sort((a, b) => (a.file < b.file ? -1 : a.file > b.file ? 1 : a.line - b.line));
      }
      rows.push(row);
    }
    ts.forEachChild(node, visit);
    if (name) stack.pop();
  };
  visit(sf);
}
rows.sort((a, b) => (a.file < b.file ? -1 : a.file > b.file ? 1 : a.line - b.line || a.col - b.col ||
  String(a.name).localeCompare(String(b.name))));
const sortKeys = (v) => Array.isArray(v) ? v.map(sortKeys) : (v && typeof v === "object" ?
  Object.keys(v).sort().reduce((o, k) => { o[k] = sortKeys(v[k]); return o; }, {}) : v);
const doc = { schema: "call-linking/truth/1", resolver: { name: "typescript", version: ts.version, js: withJs,
  conditions, tsconfig: tsconfig ? path.resolve(tsconfig).split(path.sep).join("/") : null, override: !!flag("--any-ts") },
  root: root.split(path.sep).join("/"), subdir: sub, n_files: nfiles, rows };
fs.mkdirSync(path.dirname(path.resolve(out)), { recursive: true });
fs.writeFileSync(out, JSON.stringify(sortKeys(doc)) + "\n");
console.error(`[truth_ts] files ${nfiles} call sites ${rows.length} resolved-in-corpus ${rows.filter((r) => r.defs.length).length}`);
