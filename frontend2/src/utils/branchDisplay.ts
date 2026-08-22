import type { Branch } from '../api';

const CULTURE_PROVIDER_CODES = new Set([
  'HOMEPLUS',
  'EMART',
  'LOTTE',
  'LOTTE_MART',
  'AK_PLAZA',
  'HYUNDAI_DEPT',
  'SHINSEGAE_ACADEMY',
  'GALLERIA',
  'ELAND_RETAIL',
]);

const PROVIDER_LABELS: Record<string, string> = {
  HOMEPLUS: '홈플러스 문화센터',
  EMART: '이마트 문화센터',
  LOTTE: '롯데 문화센터',
  LOTTE_MART: '롯데마트 문화센터',
  AK_PLAZA: 'AK플라자 문화아카데미',
  HYUNDAI_DEPT: '현대백화점 문화센터',
  SHINSEGAE_ACADEMY: '신세계 아카데미',
  GALLERIA: '갤러리아 문화센터',
  ELAND_RETAIL: '이랜드리테일 문화센터',
};

const BRANCH_NAME_ALIASES: Record<string, string> = {
  gwanggyo: '광교점',
};

const PROVIDER_BRANCH_NAME_ALIASES: Record<string, Record<string, string>> = {
  AK_PLAZA: {
    '03': '분당점',
  },
  GALLERIA: {
    gwanggyo: '광교점',
  },
  LOTTE_MART: {
    '322': '송파점',
  },
};

const REGION_PREFIX_PATTERN =
  /^(서울특별시|서울시|서울|부산광역시|부산시|부산|대구광역시|대구시|대구|인천광역시|인천시|인천|광주광역시|광주시|광주|대전광역시|대전시|대전|울산광역시|울산시|울산|세종특별자치시|세종시|세종|경기도|경기|강원특별자치도|강원도|강원|충청북도|충북|충청남도|충남|전북특별자치도|전라북도|전북|전라남도|전남|경상북도|경북|경상남도|경남|제주특별자치도|제주도|제주)\s+/;

function compact(value?: string | null) {
  return (value || '').replace(/\s+/g, ' ').trim();
}

function normalizeAlias(value: string) {
  const key = value.trim().toLowerCase();
  return BRANCH_NAME_ALIASES[key] || value;
}

function normalizeBranchName(value?: string | null, provider?: string | null) {
  const name = compact(value);
  if (!name) return '';

  const providerCode = compact(provider).toUpperCase();
  const providerAliases = PROVIDER_BRANCH_NAME_ALIASES[providerCode];
  if (providerAliases) {
    return providerAliases[name] || providerAliases[name.toLowerCase()] || normalizeAlias(name);
  }

  return normalizeAlias(name);
}

export function isCultureProvider(provider?: string | null, primaryCategory?: string | null) {
  const code = compact(provider).toUpperCase();
  const category = compact(primaryCategory).toUpperCase();
  return CULTURE_PROVIDER_CODES.has(code) || category === 'CULTURE_CENTER';
}

export function cultureProviderLabel(provider?: string | null, fallback?: string | null) {
  const code = compact(provider).toUpperCase();
  return PROVIDER_LABELS[code] || compact(fallback) || '문화센터';
}

export function cultureBranchName(name?: string | null) {
  const original = compact(name);
  if (!original) return '지점명 미정';
  return normalizeBranchName(original);
}

function splitEducationName(name?: string | null) {
  const text = normalizeAlias(compact(name));
  if (!text) return { title: '기관명 미정', region: '' };

  const parts = text.split(/\s*[|/>]\s*/).map(compact).filter(Boolean);
  if (parts.length >= 2) {
    return {
      title: normalizeAlias(parts[parts.length - 1]),
      region: parts.slice(0, -1).join(' · '),
    };
  }

  const regionMatch = text.match(REGION_PREFIX_PATTERN);
  if (regionMatch) {
    const region = compact(regionMatch[0]);
    const title = compact(text.slice(regionMatch[0].length));
    if (title) return { title: normalizeAlias(title), region };
  }

  return { title: text, region: '' };
}

function isPublicEducationProvider(provider?: string | null, primaryCategory?: string | null) {
  const code = compact(provider).toUpperCase();
  const category = compact(primaryCategory);
  return code.startsWith('MUNI_') || /(?:\uAD50\uC721|\uD3C9\uC0DD\uD559\uC2B5|\uACF5\uACF5\uAC15\uC88C)/.test(category);
}

function usefulProviderLabel(providerLabel?: string | null, provider?: string | null) {
  const label = compact(providerLabel);
  if (!label) return '';
  const codeLabel = compact(provider).replace(/_/g, ' ').toLowerCase();
  const lower = label.toLowerCase();
  if (codeLabel && lower === codeLabel) return '';
  if (lower.startsWith('muni ')) return '';
  return label;
}

function normalizeEducationVenueName(name: string, providerLabel: string) {
  const text = name
    .replace(/\s*\((?:\uC9C0\uD558|\uC9C0\uC0C1)?\s*\d+\s*\uCE35[^)]*\)\s*$/g, '')
    .replace(/\s+/g, ' ')
    .trim();

  if (!text) return providerLabel;
  if (/(?:\uC628\uB77C\uC778|\uBE44\uB300\uBA74|\uC790\uD0DD|\uD654\uC0C1|zoom)/i.test(text)) {
    return providerLabel || text;
  }

  const schoolMatch = text.match(
    /^(.+?(?:\uC5EC\uC790\uB300\uD559\uAD50|\uACFC\uD559\uAE30\uC220\uB300\uD559\uAD50|\uAD50\uC721\uB300\uD559\uAD50|\uC804\uBB38\uB300\uD559|\uB300\uD559\uAD50|\uB300\uD559|\uB300))(?=[\s(]|$)/,
  );
  if (schoolMatch?.[1]) return compact(schoolMatch[1]);

  const institutionMatch = text.match(
    /^(.+?(?:\uD589\uC815\uBCF5\uC9C0\uC13C\uD130|\uD3C9\uC0DD\uD559\uC2B5\uC13C\uD130|\uD3C9\uC0DD\uD559\uC2B5\uAD00|\uCCAD\uC18C\uB144\uBB38\uD654\uC758\uC9D1|\uCCAD\uC18C\uB144\uC218\uB828\uAD00|\uC8FC\uBBFC\uC13C\uD130|\uB3C4\uC11C\uAD00|\uBCF5\uC9C0\uAD00|\uC5EC\uC131\uD50C\uB77C\uC790|\uC2DC\uCCAD|\uAD6C\uCCAD|\uAD70\uCCAD))(?:[\s(]|$)/,
  );
  if (institutionMatch?.[1]) return compact(institutionMatch[1]);

  const stripped = text.replace(
    /\s+(?:[A-Za-z]?\d+(?:-\d+)?\s*\uD638?|\d+\s*\uCE35|[\w-]*\uAC15\uC758\uC2E4|[\w-]*\uC2E4\uC2B5\uC2E4|[\w-]*\uC138\uBBF8\uB098\uC2E4|\uC694\uB9AC\uAD50\uC2E4|\uCEE4\uB9AC\uC5B4\uB7A9).*$/i,
    '',
  );
  return compact(stripped) || text;
}

export function branchDisplayName(
  branch: Pick<Branch, 'name' | 'provider' | 'provider_label' | 'primary_collection_category'>,
) {
  const name = normalizeBranchName(branch.name, branch.provider);
  const providerLabel = usefulProviderLabel(branch.provider_label, branch.provider);
  if (!isCultureProvider(branch.provider, branch.primary_collection_category) && isPublicEducationProvider(branch.provider, branch.primary_collection_category)) {
    return normalizeEducationVenueName(name, providerLabel) || providerLabel || '\uAE30\uAD00\uBA85 \uBBF8\uC815';
  }
  return name || (isCultureProvider(branch.provider, branch.primary_collection_category) ? '지점명 미정' : '기관명 미정');
}

export function branchDisplaySubtext(
  branch: Pick<Branch, 'name' | 'provider' | 'provider_label' | 'address' | 'primary_collection_category'>,
) {
  if (isCultureProvider(branch.provider, branch.primary_collection_category)) {
    return cultureProviderLabel(branch.provider, branch.provider_label);
  }

  const split = splitEducationName(branch.name);
  const address = compact(branch.address);
  const provider = compact(branch.provider_label) || compact(branch.provider);
  return split.region || address || provider || '교육·체험';
}
