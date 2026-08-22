export type ProviderIconSize = 'small' | 'medium' | 'large';

export type ProviderIconInfo = {
  key: string;
  label: string;
  title: string;
  group: 'culture' | 'education' | 'unknown';
  accent: string;
};

const DEFAULT_MINT = '#14B8A6';

function sourceText(...values: Array<string | null | undefined>) {
  return values
    .filter((value): value is string => Boolean(value && value.trim()))
    .join(' ')
    .toLowerCase();
}

function includesAny(source: string, keywords: string[]) {
  return keywords.some((keyword) => source.includes(keyword.toLowerCase()));
}

function cultureIcon(key: string, label: string, title: string, accent: string): ProviderIconInfo {
  return { key, label, title, group: 'culture', accent };
}

export function getProviderIcon(
  providerName?: string | null,
  providerType?: string | null,
  centerName?: string | null,
): ProviderIconInfo {
  const source = sourceText(providerName, providerType, centerName);
  const code = (providerType || providerName || '').trim().toUpperCase();

  if (code === 'H' || code === 'HOMEPLUS' || includesAny(source, ['homeplus', '홈플러스', '홈플'])) {
    return cultureIcon('homeplus', '홈플', '홈플러스 문화센터', '#EF4444');
  }
  if (code === 'E' || code === 'EMART' || includesAny(source, ['emart', 'e-mart', '이마트'])) {
    return cultureIcon('emart', '이마트', '이마트 문화센터', '#F59E0B');
  }
  if (
    code === 'L' ||
    code === 'LOTTE' ||
    includesAny(source, ['lotte shopping', '롯데백화점', '롯데문화센터', '롯데 문화센터'])
  ) {
    return cultureIcon('lotte', '롯데', '롯데 문화센터', '#FB7185');
  }
  if (code === 'M' || code === 'LOTTE_MART' || includesAny(source, ['lotte_mart', 'lotte mart', '롯데마트'])) {
    return cultureIcon('lotte-mart', '롯데', '롯데마트 문화센터', '#FB7185');
  }
  if (code === 'A' || code === 'AK_PLAZA' || includesAny(source, ['ak plaza', 'ak플라자', 'ak 플라자'])) {
    return cultureIcon('ak', 'AK', 'AK플라자 문화아카데미', '#3B82F6');
  }
  if (code === 'HD' || code === 'HYUNDAI_DEPT' || includesAny(source, ['hyundai', '현대백화점', '현대 문화센터', '현대문화센터'])) {
    return cultureIcon('hyundai', '현대', '현대백화점 문화센터', '#16A34A');
  }
  if (code === 'S' || code === 'SHINSEGAE_ACADEMY' || includesAny(source, ['shinsegae', '신세계', '아카데미'])) {
    return cultureIcon('shinsegae', '신세계', '신세계 아카데미', '#0F766E');
  }
  if (code === 'G' || code === 'GALLERIA' || includesAny(source, ['galleria', '갤러리아'])) {
    return cultureIcon('galleria', '갤러리아', '갤러리아 문화센터', '#64748B');
  }
  if (code === 'ER' || code === 'ELAND_RETAIL' || includesAny(source, ['eland', '이랜드'])) {
    return cultureIcon('eland', 'ER', '이랜드리테일 문화센터', DEFAULT_MINT);
  }

  if (
    code === 'P' ||
    code === 'PUBLIC' ||
    code.startsWith('MUNI_') ||
    includesAny(source, [
      'muni_',
      'public',
      'municipal',
      'city',
      'go.kr',
      '공공',
      '시청',
      '구청',
      '군청',
      '평생학습',
      '시설관리공단',
    ])
  ) {
    return { key: 'public', label: '공', title: '공공기관', group: 'education', accent: DEFAULT_MINT };
  }

  if (includesAny(source, ['library', '도서관', 'lib_'])) {
    return { key: 'library', label: '도', title: '도서관', group: 'education', accent: DEFAULT_MINT };
  }
  if (includesAny(source, ['museum', '박물관', '미술관'])) {
    return { key: 'museum', label: '박', title: '박물관·미술관', group: 'education', accent: '#F59E0B' };
  }
  if (includesAny(source, ['science', '과학관', '과학'])) {
    return { key: 'science', label: '과', title: '과학관', group: 'education', accent: '#1D9BF0' };
  }
  if (includesAny(source, ['youth', '청소년', '청년'])) {
    return { key: 'youth', label: '청', title: '청소년센터', group: 'education', accent: '#8B5CF6' };
  }
  if (includesAny(source, ['experience', '체험', '행사', '예약'])) {
    return { key: 'experience', label: '체', title: '체험행사', group: 'education', accent: DEFAULT_MINT };
  }
  if (code === 'CULTURE_FACILITY') {
    return { key: 'experience', label: '체', title: '문화기반시설', group: 'education', accent: DEFAULT_MINT };
  }
  if (includesAny(source, ['one day', 'oneday', '원데이', '일일'])) {
    return { key: 'oneday', label: '원', title: '원데이', group: 'education', accent: DEFAULT_MINT };
  }
  if (includesAny(source, ['culture_center', 'culture center', '문화센터'])) {
    return cultureIcon('culture-other', 'MC', '기타 문화센터', DEFAULT_MINT);
  }
  if (includesAny(source, ['education', '교육', '강좌', '강의'])) {
    return { key: 'education-other', label: '기', title: '기타 교육·체험 기관', group: 'education', accent: DEFAULT_MINT };
  }

  return cultureIcon('unknown', 'MC', '기타 문화센터', DEFAULT_MINT);
}
