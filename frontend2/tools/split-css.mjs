import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

const inputPath = resolve(process.cwd(), process.argv[2] || '.css-audit/styles.pruned.css');
const outputDirectory = resolve(process.cwd(), 'src/styles');
const source = readFileSync(inputPath, 'utf8');

const sectionDefinitions = [
  {
    marker: '/* Core tokens, shared controls, cards, dialogs, and filter primitives. Keep import order in ../styles.css. */',
    file: 'core.css',
    heading: 'Core tokens, shared controls, cards, dialogs, and filter primitives.',
  },
  {
    marker: '/* Search, dashboard, branch, map, and course presentation. Keep import order in ../styles.css. */',
    file: 'dashboard.css',
    heading: 'Search, dashboard, branch, map, and course presentation.',
  },
  {
    marker: '/* Current dashboard refinements and interactive states. Keep import order in ../styles.css. */',
    file: 'dashboard-current.css',
    heading: 'Current dashboard refinements and interactive states.',
  },
  {
    marker: '/* Mobile home, responsive containment, accessibility, and narrow-screen safeguards. Keep import order in ../styles.css. */',
    file: 'responsive.css',
    heading: 'Mobile home, responsive containment, accessibility, and narrow-screen safeguards.',
  },
];

const markerIndexes = sectionDefinitions.map(({ marker }) => {
  const index = source.indexOf(marker);
  if (index < 0) throw new Error(`CSS split marker not found: ${marker}`);
  return index;
});
markerIndexes[0] = 0;

const cleanSection = (content) => content
  .replace(/\/\*(?![!])[\s\S]*?\*\//g, '')
  .replace(/[ \t]+\n/g, '\n')
  .replace(/\n{3,}/g, '\n\n')
  .trim();

const sections = sectionDefinitions.map(({ file, heading }, index) => ({
  file,
  heading,
  content: source.slice(markerIndexes[index], markerIndexes[index + 1] ?? source.length),
}));

mkdirSync(outputDirectory, { recursive: true });
sections.forEach(({ file, heading, content }) => {
  const banner = `/* ${heading} Keep import order in ../styles.css. */\n\n`;
  writeFileSync(resolve(outputDirectory, file), `${banner}${cleanSection(content)}\n`);
});

console.log(JSON.stringify({
  input: inputPath,
  outputDirectory,
  files: sections.map(({ file, content }) => ({
    file,
    bytes: Buffer.byteLength(content),
    lines: content.split(/\r?\n/).length,
  })),
}, null, 2));
