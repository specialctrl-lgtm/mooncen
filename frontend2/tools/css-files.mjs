import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';

const importPattern = /@import\s+['"]([^'"]+\.css)['"]\s*;/g;

export function readCssGraph(entryPath) {
  const files = [];
  const active = new Set();

  const read = (path) => {
    const absolutePath = resolve(path);
    if (active.has(absolutePath)) throw new Error(`Circular CSS import: ${absolutePath}`);
    active.add(absolutePath);
    files.push(absolutePath);
    const source = readFileSync(absolutePath, 'utf8');
    const combined = source.replace(importPattern, (_statement, reference) => (
      read(resolve(dirname(absolutePath), reference))
    ));
    active.delete(absolutePath);
    return combined;
  };

  return { css: read(entryPath), files };
}
