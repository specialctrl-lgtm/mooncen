const EMPTY_TITLE_BRACKETS_RE = /\s*[\(\[\{（［｛]\s*[\)\]\}）］｝]\s*/g;
const EDGE_TITLE_NOISE_RE = /^[\s\-_*|,.)\]}>]+|[\s\-_*|,<(\[{]+$/g;
const HTML_ENTITY_RE = /&(#(?:x[\da-f]+|\d+)|[a-z][a-z\d]+);/gi;
const HTML_NAMED_ENTITIES: Record<string, string> = {
  amp: '&',
  apos: "'",
  bull: '•',
  copy: '©',
  deg: '°',
  divide: '÷',
  emsp: '\u2003',
  ensp: '\u2002',
  gt: '>',
  hellip: '…',
  laquo: '«',
  ldquo: '“',
  lsquo: '‘',
  lt: '<',
  mdash: '—',
  middot: '·',
  nbsp: '\u00a0',
  ndash: '–',
  plusmn: '±',
  quot: '"',
  raquo: '»',
  rdquo: '”',
  reg: '®',
  rsquo: '’',
  thinsp: '\u2009',
  times: '×',
  trade: '™',
};

function decodeEntity(match: string, entity: string) {
  if (!entity.startsWith('#')) return HTML_NAMED_ENTITIES[entity.toLowerCase()] ?? match;

  const hexadecimal = entity[1]?.toLowerCase() === 'x';
  const codePoint = Number.parseInt(entity.slice(hexadecimal ? 2 : 1), hexadecimal ? 16 : 10);
  if (
    !Number.isFinite(codePoint) ||
    codePoint <= 0 ||
    codePoint > 0x10ffff ||
    (codePoint >= 0xd800 && codePoint <= 0xdfff)
  ) {
    return match;
  }
  return String.fromCodePoint(codePoint);
}

export function decodeHtmlText(value?: string | null) {
  let text = String(value || '');
  for (let pass = 0; pass < 2; pass += 1) {
    const decoded = text.replace(HTML_ENTITY_RE, decodeEntity);
    if (decoded === text) break;
    text = decoded;
  }
  return text;
}

export function normalizeCourseDisplayTitle(value?: string | null, fallback = '강좌명 미정') {
  let text = decodeHtmlText(value);
  let previous = '';

  while (text !== previous) {
    previous = text;
    text = text.replace(EMPTY_TITLE_BRACKETS_RE, ' ');
  }

  text = text
    .replace(/\s+/g, ' ')
    .replace(EDGE_TITLE_NOISE_RE, '')
    .trim();

  return text || fallback;
}
