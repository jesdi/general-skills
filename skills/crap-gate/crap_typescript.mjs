#!/usr/bin/env node
// Named functions + cyclomatic complexity for TS/TSX via the TypeScript
// compiler API. Reads {"files":[{"path","label"}]} on stdin, writes a JSON
// array of {file,name,start,end,cc} on stdout. `typescript` is resolved from
// the --cwd directory's node_modules so the analyzer follows the project's
// own compiler version and needs no install of its own.
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const cwdIndex = process.argv.indexOf('--cwd');
const cwd = path.resolve(cwdIndex >= 0 ? process.argv[cwdIndex + 1] : process.cwd());
let ts;
try {
  ts = createRequire(pathToFileURL(path.join(cwd, 'package.json')))('typescript');
} catch (err) {
  console.error(`cannot load typescript from ${cwd}/node_modules: ${err.message}`);
  process.exit(3);
}

const K = ts.SyntaxKind;
const BRANCH_KINDS = new Set([
  K.IfStatement, K.ConditionalExpression, K.ForStatement, K.ForInStatement,
  K.ForOfStatement, K.WhileStatement, K.DoStatement, K.CatchClause, K.CaseClause,
]);
const LOGICAL_OPERATORS = new Set([
  K.AmpersandAmpersandToken, K.BarBarToken, K.QuestionQuestionToken,
  K.AmpersandAmpersandEqualsToken, K.BarBarEqualsToken, K.QuestionQuestionEqualsToken,
]);

const scriptKind = (file) =>
  file.endsWith('.tsx') ? ts.ScriptKind.TSX
  : file.endsWith('.jsx') ? ts.ScriptKind.JSX
  : file.endsWith('.js') || file.endsWith('.mjs') ? ts.ScriptKind.JS
  : ts.ScriptKind.TS;

const isFunctionLike = (n) =>
  ts.isFunctionDeclaration(n) || ts.isFunctionExpression(n) || ts.isArrowFunction(n) ||
  ts.isMethodDeclaration(n) || ts.isConstructorDeclaration(n) ||
  ts.isGetAccessorDeclaration(n) || ts.isSetAccessorDeclaration(n);

const hasDefaultModifier = (n) =>
  (ts.canHaveModifiers(n) ? ts.getModifiers(n) ?? [] : []).some((m) => m.kind === K.DefaultKeyword);

const memberName = (n) => (n.name && ts.isIdentifier(n.name) ? n.name.text : n.name?.getText() ?? '<computed>');

// Direct naming (any depth): declarations, class members, direct initialisers.
function directName(n, prefix) {
  if (ts.isFunctionDeclaration(n)) return n.name ? prefix + n.name.text : hasDefaultModifier(n) ? prefix + 'default' : null;
  if (ts.isConstructorDeclaration(n)) return prefix + 'constructor';
  if (ts.isMethodDeclaration(n) || ts.isGetAccessorDeclaration(n) || ts.isSetAccessorDeclaration(n)) {
    return ts.isClassLike(n.parent) ? prefix + memberName(n) : null; // object-literal methods fold
  }
  const p = n.parent;
  if (ts.isVariableDeclaration(p) && ts.isIdentifier(p.name)) return prefix + p.name.text;
  if (ts.isExportAssignment(p)) return prefix + 'default';
  return null;
}

// Module-level only: `export const X = memo(forwardRef((p, r) => …))` is X.
function wrapperName(n, prefix) {
  let node = n;
  for (;;) {
    const p = node.parent;
    if (!p) return null;
    if (ts.isCallExpression(p) && p.arguments.includes(node)) { node = p; continue; }
    if (ts.isParenthesizedExpression(p) || ts.isAsExpression(p) || ts.isSatisfiesExpression(p)) { node = p; continue; }
    if (ts.isVariableDeclaration(p) && ts.isIdentifier(p.name) && p.initializer === node) return prefix + p.name.text;
    if (ts.isExportAssignment(p)) return prefix + 'default';
    return null;
  }
}

function analyze(file, label) {
  const text = readFileSync(file, 'utf8');
  const sf = ts.createSourceFile(label, text, ts.ScriptTarget.Latest, true, scriptKind(file));
  const line = (pos) => sf.getLineAndCharacterOfPosition(pos).line + 1;
  const out = [];

  function visit(node, prefix, current) {
    if (BRANCH_KINDS.has(node.kind) || (ts.isBinaryExpression(node) && LOGICAL_OPERATORS.has(node.operatorToken.kind))) {
      if (current) current.cc += 1;
    }
    if (ts.isClassDeclaration(node) || ts.isClassExpression(node)) {
      const className = node.name ? node.name.text : '<class>';
      ts.forEachChild(node, (c) => visit(c, prefix + className + '.', current));
      return;
    }
    if (isFunctionLike(node)) {
      const name = directName(node, prefix) ?? (current ? null : wrapperName(node, prefix));
      if (name) {
        const entry = { file: label, name, start: line(node.getStart(sf)), end: line(node.getEnd()), cc: 1 };
        out.push(entry);
        ts.forEachChild(node, (c) => visit(c, name + '.', entry));
        return;
      }
      // anonymous: fold into `current` (or ignore when there is none)
    }
    ts.forEachChild(node, (c) => visit(c, prefix, current));
  }

  visit(sf, '', null);
  return out;
}

const input = JSON.parse(readFileSync(0, 'utf8'));
const results = [];
for (const { path: file, label } of input.files) results.push(...analyze(file, label));
process.stdout.write(JSON.stringify(results));
