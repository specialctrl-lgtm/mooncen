import { statSync } from 'node:fs';
import { resolve } from 'node:path';
import postcss from 'postcss';
import { readCssGraph } from './css-files.mjs';

const inputPath = resolve(process.cwd(), process.argv[2] || 'src/styles.css');
const { css: source, files } = readCssGraph(inputPath);
const root = postcss.parse(source, { from: inputPath });

const contextOf = (node) => {
  const context = [];
  let current = node.parent;
  while (current && current.type !== 'root') {
    if (current.type === 'atrule') context.unshift(`@${current.name} ${current.params}`.trim());
    current = current.parent;
  }
  return context.join(' > ');
};

const declarationSignature = (rule) => rule.nodes
  .filter((node) => node.type === 'decl')
  .map((decl) => `${decl.prop.trim()}:${decl.value.trim()}${decl.important ? '!important' : ''}`)
  .join(';');

const ruleGroups = new Map();
const exactRuleGroups = new Map();
let rules = 0;
let declarations = 0;
let important = 0;
let duplicateDeclarationsInRule = 0;
const importantByContext = new Map();

root.walkRules((rule) => {
  rules += 1;
  const context = contextOf(rule);
  const selector = rule.selector.trim();
  const selectorKey = `${context}\n${selector}`;
  const exactKey = `${selectorKey}\n${declarationSignature(rule)}`;
  const selectorEntries = ruleGroups.get(selectorKey) || [];
  selectorEntries.push(rule);
  ruleGroups.set(selectorKey, selectorEntries);
  const exactEntries = exactRuleGroups.get(exactKey) || [];
  exactEntries.push(rule);
  exactRuleGroups.set(exactKey, exactEntries);

  const seenDeclarations = new Set();
  rule.walkDecls((decl) => {
    declarations += 1;
    if (decl.important) {
      important += 1;
      importantByContext.set(context || '(base)', (importantByContext.get(context || '(base)') || 0) + 1);
    }
    const signature = `${decl.prop.trim()}\n${decl.value.trim()}\n${decl.important}`;
    if (seenDeclarations.has(signature)) duplicateDeclarationsInRule += 1;
    seenDeclarations.add(signature);
  });
});

const repeatedSelectorGroups = [...ruleGroups.values()].filter((entries) => entries.length > 1);
const exactDuplicateGroups = [...exactRuleGroups.values()].filter((entries) => entries.length > 1);
const removableExactRules = exactDuplicateGroups.reduce((sum, entries) => sum + entries.length - 1, 0);
const removableExactDeclarations = exactDuplicateGroups.reduce(
  (sum, entries) => sum + (entries.length - 1) * entries[0].nodes.filter((node) => node.type === 'decl').length,
  0,
);

const topRepeatedSelectors = repeatedSelectorGroups
  .map((entries) => ({
    selector: entries[0].selector,
    context: contextOf(entries[0]),
    occurrences: entries.length,
    declarations: entries.reduce(
      (sum, rule) => sum + rule.nodes.filter((node) => node.type === 'decl').length,
      0,
    ),
  }))
  .sort((left, right) => right.declarations - left.declarations)
  .slice(0, 30);

console.log(JSON.stringify({
  file: inputPath,
  files,
  bytes: files.reduce((sum, file) => sum + statSync(file).size, 0),
  lines: source.split(/\r?\n/).length,
  rules,
  declarations,
  important,
  repeatedSelectorGroups: repeatedSelectorGroups.length,
  exactDuplicateGroups: exactDuplicateGroups.length,
  removableExactRules,
  removableExactDeclarations,
  duplicateDeclarationsInRule,
  topImportantContexts: [...importantByContext.entries()]
    .map(([context, count]) => ({ context, count }))
    .sort((left, right) => right.count - left.count)
    .slice(0, 20),
  topRepeatedSelectors,
}, null, 2));
