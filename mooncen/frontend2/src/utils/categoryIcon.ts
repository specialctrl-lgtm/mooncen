export type CategoryIconGroup = 'culture' | 'education';

export type CategoryIconInfo = {
  key: string;
  label: string;
  color: string;
  background: string;
  path: string;
};

const mintColor = '#0F9F95';
const mintBackground = '#F0FDFA';

const iconPaths = {
  search: '<circle cx="21" cy="21" r="9"/><path d="m28 28 8 8"/>',
  location: '<path d="M24 39s10-9.2 10-18a10 10 0 0 0-20 0c0 8.8 10 18 10 18Z"/><circle cx="24" cy="21" r="3"/>',
  calendar: '<rect x="13" y="16" width="22" height="20" rx="3"/><path d="M18 12v7M30 12v7M13 23h22"/><circle cx="19" cy="28" r="1.5"/><circle cx="24" cy="28" r="1.5"/><circle cx="29" cy="28" r="1.5"/>',
  ticket: '<path d="M15 18 32 13l4 8a4 4 0 0 0 2 7l4 8-17 5-4-8a4 4 0 0 0-2-7l-4-8Z"/><path d="m28 19 5 16"/>',
  star: '<path d="m24 13 3.3 7 7.7 1-5.6 5.4 1.4 7.6-6.8-3.7-6.8 3.7 1.4-7.6L13 21l7.7-1L24 13Z"/>',
  heart: '<path d="M24 36s-11-6.7-11-15a6 6 0 0 1 11-3.4A6 6 0 0 1 35 21c0 8.3-11 15-11 15Z"/>',
  thumbsUp: '<path d="M18 22v17h-5V22h5Z"/><path d="M18 24c4-3 6-7 6-12 4 0 5 2 5 5 0 2-.5 4-1 5h7c2 0 3 1.5 2.5 3.5l-2.2 9C34.8 37 33 39 30 39H18V24Z"/>',
  group: '<circle cx="24" cy="20" r="5"/><circle cx="15" cy="23" r="4"/><circle cx="33" cy="23" r="4"/><path d="M14 36c2-5 5-8 10-8s8 3 10 8M8 36c1.5-4 4-6 8-6M32 30c4 0 6.5 2 8 6"/>',
  person: '<circle cx="24" cy="18" r="6"/><path d="M14 38c2-7 6-11 10-11s8 4 10 11"/>',
  flag: '<path d="M16 39V13"/><path d="M18 15h16l-3 6 3 6H18V15Z"/>',
  tag: '<path d="M15 16h15l8 8-15 15-8-8V16Z"/><circle cx="27" cy="23" r="2"/>',
  percent: '<path d="M15 34 33 14"/><circle cx="17" cy="17" r="3"/><circle cx="31" cy="31" r="3"/>',
  camera: '<path d="M14 19h7l2-4h6l2 4h3a4 4 0 0 1 4 4v12a4 4 0 0 1-4 4H14a4 4 0 0 1-4-4V23a4 4 0 0 1 4-4Z"/><circle cx="24" cy="29" r="6"/>',
  image: '<rect x="12" y="15" width="24" height="20" rx="3"/><path d="m16 31 6-6 5 5 3-3 6 6"/><circle cx="19" cy="21" r="2"/>',
  utensils: '<path d="M18 14v13M14 14v9c0 2 2 4 4 4s4-2 4-4v-9M18 27v13"/><path d="M31 14v26M31 14c5 3 6 11 0 15"/>',
  coffee: '<path d="M14 20h18v8a8 8 0 0 1-8 8h-2a8 8 0 0 1-8-8v-8Z"/><path d="M32 22h2a4 4 0 0 1 0 8h-2M13 39h22"/>',
  gift: '<rect x="14" y="22" width="20" height="16" rx="2"/><path d="M24 22v16M14 28h20"/><path d="M24 22c-5-2-7-7-3-8 3-1 5 3 3 8ZM24 22c5-2 7-7 3-8-3-1-5 3-3 8Z"/>',
  bag: '<path d="M15 20h18l2 19H13l2-19Z"/><path d="M19 20a5 5 0 0 1 10 0"/>',
  megaphone: '<path d="M13 29h5l16-8v18l-16-8h-5v-2Z"/><path d="m18 31 3 8M37 25l4-3M38 30h5M37 35l4 3"/>',
  graduation: '<path d="m12 21 12-6 12 6-12 6-12-6Z"/><path d="M17 24v7c4 4 10 4 14 0v-7M36 21v10"/>',
  book: '<path d="M14 16c6 0 9 2 10 5v20c-2-3-5-5-10-5V16Z"/><path d="M24 21c1-3 4-5 10-5v20c-5 0-8 2-10 5V21Z"/>',
  art: '<path d="M17 32c-5-3-4-12 2-16 7-4 18 0 17 8-.4 4-4 5-7 4-2-.5-3 .3-3 2 0 4-5 5-9 2Z"/><circle cx="21" cy="22" r="1.8"/><circle cx="28" cy="20" r="1.8"/><circle cx="33" cy="25" r="1.8"/><path d="M32 35 39 18"/>',
  baby: '<circle cx="24" cy="24" r="10"/><path d="M21 24h.1M27 24h.1M20 29c2 2 6 2 8 0M24 10c-1 2-1 4 2 5M14 24h-2M36 24h-2"/>',
  balloon: '<path d="M24 13c6 0 10 5 10 11 0 7-5 11-10 11s-10-4-10-11c0-6 4-11 10-11Z"/><path d="m22 35 4 0M24 35v6"/>',
  bicycle: '<circle cx="16" cy="32" r="7"/><circle cx="34" cy="32" r="7"/><path d="M16 32h8l6-10h-8M24 32l-5-12M30 22l4 10M19 20h6"/>',
  car: '<path d="M14 28h20l-3-7H17l-3 7Z"/><path d="M12 28v8h24v-8M17 36v3M31 36v3"/><circle cx="18" cy="32" r="2"/><circle cx="30" cy="32" r="2"/>',
  train: '<rect x="16" y="13" width="16" height="25" rx="4"/><path d="M20 18h8M19 27h10M20 38l-4 5M28 38l4 5"/><circle cx="20" cy="33" r="1.5"/><circle cx="28" cy="33" r="1.5"/>',
  museum: '<path d="M14 25h20M17 25v14M24 25v14M31 25v14M13 39h22M16 21l8-6 8 6H16Z"/>',
  tree: '<path d="M24 37V25"/><path d="M24 25c-7 0-10-4-8-9 5 0 8 3 8 9Z"/><path d="M24 25c7 0 10-4 8-9-5 0-8 3-8 9Z"/><path d="M18 39h12"/>',
  shield: '<path d="M24 13 35 18v8c0 7-4.5 12-11 15-6.5-3-11-8-11-15v-8l11-5Z"/><path d="m19 27 4 4 7-8"/>',
  music: '<path d="M20 34a4 4 0 1 1-2-3.5V17l15-3v15a4 4 0 1 1-2-3.5V20l-11 2v12Z"/>',
  sports: '<path d="M15 33h18M19 19v12M29 19v12M17 19h14M17 31h14"/><path d="M15 23h18M15 27h18"/>',
  science: '<path d="M21 13v12l-8 14h22l-8-14V13"/><path d="M18 13h12M18 34h12"/><circle cx="25" cy="31" r="1.8"/>',
  coding: '<path d="M14 34h20M17 34h14l2-18H15l2 18Z"/><path d="m22 22-3 3 3 3M27 22l3 3-3 3"/>',
  nature: '<path d="M17 32c9 0 15-6 15-15-9 0-15 6-15 15Z"/><path d="M31 17c5 3 8 7 7 14-7 0-12-4-15-10M25 26 16 36"/>',
  performance: '<path d="M15 18c4-3 9-3 13 0v12c-4 3-9 3-13 0V18Z"/><path d="M28 18c4-3 9-3 13 0v12c-4 3-9 3-13 0M19 24h.1M24 24h.1M18 30c2-1 5-1 7 0"/>',
  career: '<path d="M15 33 34 15M17 17l19 19"/><path d="m16 33 3 8 5-6M33 15l1-5 5 3-4 3"/>',
  social: '<circle cx="20" cy="21" r="5"/><circle cx="32" cy="21" r="5"/><path d="M12 36c2-6 6-9 12-9M28 27c6 0 10 3 12 9"/>',
  camp: '<path d="M13 38 24 16l11 22H13Z"/><path d="M24 16v22M17 38l7-13 7 13"/>',
  other: '<circle cx="18" cy="24" r="2"/><circle cx="24" cy="24" r="2"/><circle cx="30" cy="24" r="2"/>',
} satisfies Record<string, string>;

type CategoryDef = {
  key: string;
  label: string;
  path: string;
  keywords: string[];
};

const defs: CategoryDef[] = [
  { key: 'all', label: '전체', path: iconPaths.search, keywords: ['전체', 'all'] },
  { key: 'baby', label: '영유아·놀이', path: iconPaths.baby, keywords: ['영유아', '영아', '유아', '엄마', '아기', '개월', '오감', '놀이', 'kids', 'baby'] },
  { key: 'child', label: '유아', path: iconPaths.baby, keywords: ['유아', '아동', '어린이', '키즈'] },
  { key: 'elementary', label: '초등', path: iconPaths.graduation, keywords: ['초등', '학생', '방학'] },
  { key: 'language', label: '어학·독서', path: iconPaths.book, keywords: ['어학', '언어', '영어', '중국어', '일본어', '독서', '글쓰기'] },
  { key: 'art', label: '미술·공예', path: iconPaths.art, keywords: ['미술', '공예', '그림', '드로잉', '만들기'] },
  { key: 'music', label: '음악·악기', path: iconPaths.music, keywords: ['음악', '악기', '노래', '피아노'] },
  { key: 'sports', label: '무용·댄스·운동', path: iconPaths.sports, keywords: ['무용', '댄스', '체육', '스포츠', '운동', '축구', '수영', '발레', '요가'] },
  { key: 'cooking', label: '요리·베이킹', path: iconPaths.utensils, keywords: ['요리', '베이킹', '쿠킹', '쿠키'] },
  { key: 'science', label: '과학·창의', path: iconPaths.science, keywords: ['과학', '창의', '실험', '과학관'] },
  { key: 'coding', label: '코딩', path: iconPaths.coding, keywords: ['코딩', '로봇', 'it', 'IT', '융합', 'AI', '소프트웨어'] },
  { key: 'photo', label: '디지털·사진', path: iconPaths.camera, keywords: ['디지털', '사진', '영상', '미디어', '컴퓨터', '스마트폰'] },
  { key: 'beauty', label: '뷰티·생활', path: iconPaths.heart, keywords: ['뷰티', '생활', '건강', '힐링', '메이크업', '네일'] },
  { key: 'economy', label: '재테크·경제', path: iconPaths.percent, keywords: ['재테크', '경제', '금융', '부동산'] },
  { key: 'certificate', label: '자격·전문', path: iconPaths.career, keywords: ['자격', '전문', '창업', '강사'] },
  { key: 'humanities', label: '인문·전통문화', path: iconPaths.museum, keywords: ['인문', '전통', '역사', '문화유산'] },
  { key: 'culture-event', label: '체험·이벤트', path: iconPaths.ticket, keywords: ['체험', '이벤트', '캠프', '탐방'] },
  { key: 'hobby', label: '취미·여가', path: iconPaths.star, keywords: ['취미', '여가', '바둑', '보드게임', '마술'] },
  { key: 'sensory', label: '오감놀이', path: iconPaths.baby, keywords: ['오감', '놀이', '감각'] },
  { key: 'nature', label: '자연·생태', path: iconPaths.nature, keywords: ['자연', '생태', '숲', '수목원', '환경'] },
  { key: 'media', label: '사진·영상', path: iconPaths.camera, keywords: ['사진', '영상', '미디어'] },
  { key: 'tradition', label: '공예·전통', path: iconPaths.museum, keywords: ['전통', '문화재', '역사', '도예', '공예'] },
  { key: 'performance', label: '연기·표현', path: iconPaths.performance, keywords: ['연기', '표현', '공연', '연극'] },
  { key: 'career', label: '진로·직업', path: iconPaths.graduation, keywords: ['진로', '직업', '취업'] },
  { key: 'public', label: '공공강좌', path: iconPaths.museum, keywords: ['공공', '평생', '강좌', '교육'] },
  { key: 'library', label: '도서관', path: iconPaths.book, keywords: ['도서관', '독서', '인문', 'library'] },
  { key: 'museum', label: '박물관', path: iconPaths.museum, keywords: ['박물관', '미술관', '전시', 'museum'] },
  { key: 'experience', label: '체험', path: iconPaths.ticket, keywords: ['체험', '행사', '예약', '탐방', '문화재단'] },
  { key: 'oneday', label: '원데이', path: iconPaths.star, keywords: ['원데이', '일일', '1일', '특강'] },
  { key: 'welfare', label: '복지관', path: iconPaths.group, keywords: ['복지관', '복지', '사회복지'] },
  { key: 'camp', label: '캠프·야외', path: iconPaths.camp, keywords: ['캠프', '야외', '탐방'] },
];

function normalize(value: string) {
  return String(value || '').trim().toLowerCase().replace(/\s+/g, '');
}

export function getCategoryIcon(labelOrValue: string, group: CategoryIconGroup = 'culture'): CategoryIconInfo {
  const raw = String(labelOrValue || '');
  const source = normalize(raw);
  const matched = defs.find((item) => (
    normalize(item.key) === source ||
    normalize(item.label) === source ||
    item.keywords.some((keyword) => source.includes(normalize(keyword)) || normalize(keyword).includes(source))
  ));

  if (matched) {
    return {
      key: matched.key,
      label: matched.label,
      color: mintColor,
      background: mintBackground,
      path: matched.path,
    };
  }

  return {
    key: group === 'education' ? 'education-other' : 'culture-other',
    label: '기타',
    color: mintColor,
    background: mintBackground,
    path: iconPaths.other,
  };
}
