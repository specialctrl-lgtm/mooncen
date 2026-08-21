import { mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { dirname, extname, join, resolve } from 'node:path';
import postcss from 'postcss';
import { PurgeCSS } from 'purgecss';
import { readCssGraph } from './css-files.mjs';

const sourcePath = resolve(process.cwd(), 'src/styles.css');
const stripImportant = process.argv.includes('--strip-important');
const keepResponsiveImportant = process.argv.includes('--keep-responsive-important');
const keepFilterImportant = process.argv.includes('--keep-filter-important');
const outputArgument = process.argv.find((argument) => !argument.startsWith('--') && argument !== process.argv[0] && argument !== process.argv[1]);
const outputPath = resolve(process.cwd(), outputArgument || '.css-audit/styles.pruned.css');

function sourceFiles(directory) {
  return readdirSync(directory).flatMap((name) => {
    const path = join(directory, name);
    return statSync(path).isDirectory()
      ? sourceFiles(path)
      : ['.ts', '.tsx'].includes(extname(path))
        ? [path]
        : [];
  });
}

function contextOf(node) {
  const context = [];
  let current = node.parent;
  while (current && current.type !== 'root') {
    if (current.type === 'atrule') context.unshift(`@${current.name} ${current.params}`.trim());
    current = current.parent;
  }
  return context.join(' > ');
}

function declarationSignature(rule) {
  return rule.nodes
    .filter((node) => node.type === 'decl')
    .map((decl) => `${decl.prop.trim()}:${decl.value.trim()}${decl.important ? '!important' : ''}`)
    .join(';');
}

function removeEarlierExactRuleDuplicates(root) {
  const groups = new Map();
  root.walkRules((rule) => {
    const key = `${contextOf(rule)}\n${rule.selector.trim()}\n${declarationSignature(rule)}`;
    const rules = groups.get(key) || [];
    rules.push(rule);
    groups.set(key, rules);
  });

  let removed = 0;
  groups.forEach((rules) => {
    rules.slice(0, -1).forEach((rule) => {
      rule.remove();
      removed += 1;
    });
  });
  return removed;
}

function removeEarlierRepeatedDeclarations(root) {
  const groups = new Map();
  root.walkRules((rule) => {
    const key = `${contextOf(rule)}\n${rule.selector.trim()}`;
    const rules = groups.get(key) || [];
    rules.push(rule);
    groups.set(key, rules);
  });

  let removed = 0;
  groups.forEach((rules) => {
    const seen = new Set();
    [...rules].reverse().forEach((rule) => {
      [...rule.nodes].reverse().forEach((node) => {
        if (node.type !== 'decl') return;
        const signature = `${node.prop.trim()}\n${node.value.trim()}\n${node.important}`;
        if (seen.has(signature)) {
          node.remove();
          removed += 1;
        } else {
          seen.add(signature);
        }
      });
      if (!rule.nodes.some((node) => node.type === 'decl')) rule.remove();
    });
  });
  return removed;
}

function removeDominatedDeclarations(root) {
  const groups = new Map();
  root.walkRules((rule) => {
    const selectorKey = `${contextOf(rule)}\n${rule.selector.trim()}`;
    rule.nodes.forEach((node) => {
      if (node.type !== 'decl') return;
      const key = `${selectorKey}\n${node.prop.trim().toLowerCase()}`;
      const entries = groups.get(key) || [];
      entries.push({ declaration: node, rule });
      groups.set(key, entries);
    });
  });

  let removed = 0;
  groups.forEach((entries) => {
    if (entries.length < 2) return;
    const declarationsPerRule = new Map();
    entries.forEach(({ rule }) => declarationsPerRule.set(rule, (declarationsPerRule.get(rule) || 0) + 1));
    if ([...declarationsPerRule.values()].some((count) => count > 1)) return;

    const importantEntries = entries.filter(({ declaration }) => declaration.important);
    const keeper = importantEntries.length ? importantEntries.at(-1) : entries.at(-1);
    entries.forEach((entry) => {
      if (entry === keeper) return;
      entry.declaration.remove();
      removed += 1;
    });
  });

  root.walkRules((rule) => {
    if (!rule.nodes.some((node) => node.type === 'decl')) rule.remove();
  });
  return removed;
}

function removeEmptyContainers(root) {
  let removed = 0;
  let changed = true;
  while (changed) {
    changed = false;
    root.walkAtRules((atRule) => {
      if (atRule.nodes && atRule.nodes.length === 0) {
        atRule.remove();
        removed += 1;
        changed = true;
      }
    });
  }
  return removed;
}

const content = sourceFiles(resolve(process.cwd(), 'src')).map((path) => ({
  raw: readFileSync(path, 'utf8'),
  extension: extname(path).slice(1),
}));
const { css: source, files: sourceFilesList } = readCssGraph(sourcePath);
const [purged] = await new PurgeCSS().purge({
  content,
  css: [{ raw: source }],
  rejected: true,
  safelist: {
    standard: [
      /^status-/,
      /^source-/,
      /^type-/,
      /^scope-icon-/,
      /^radius-range-icon-/,
      /^provider-icon-/,
      /^category-icon-/,
      /^(provider|education|experience)-mode$/,
    ],
  },
});

const root = postcss.parse(purged.css, { from: sourcePath, to: outputPath });
const removedExactRules = removeEarlierExactRuleDuplicates(root);
const removedRepeatedDeclarations = removeEarlierRepeatedDeclarations(root);
const removedDominatedDeclarations = removeDominatedDeclarations(root);
let removedImportant = 0;
if (stripImportant) {
  root.walkDecls((declaration) => {
    if (!declaration.important) return;
    if (keepResponsiveImportant && contextOf(declaration).includes('max-width')) return;
    if (
      keepFilterImportant
      && declaration.parent?.type === 'rule'
      && /(sidebar|filter|scope|category|quick|calendar|age-month)/.test(declaration.parent.selector)
    ) return;
    declaration.important = false;
    removedImportant += 1;
  });
}
const removedEmptyContainers = removeEmptyContainers(root);
const output = root.toString();

mkdirSync(dirname(outputPath), { recursive: true });
writeFileSync(outputPath, output);

console.log(JSON.stringify({
  source: sourcePath,
  sourceFiles: sourceFilesList,
  output: outputPath,
  sourceBytes: Buffer.byteLength(source),
  outputBytes: Buffer.byteLength(output),
  rejectedSelectors: purged.rejected?.length || 0,
  removedExactRules,
  removedRepeatedDeclarations,
  removedDominatedDeclarations,
  removedImportant,
  removedEmptyContainers,
}, null, 2));
