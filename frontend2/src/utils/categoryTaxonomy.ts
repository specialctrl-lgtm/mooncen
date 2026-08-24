export type CategoryMode = 'provider' | 'education' | 'experience';

export const cultureStandardCategoryLabels = [
  '영유아·놀이',
  '미술·공예',
  '음악·악기',
  '무용·댄스·운동',
  '요리·베이킹',
  '어학·독서',
  '과학·창의',
  '디지털·사진',
  '뷰티·생활',
  '재테크·경제',
  '자격·전문',
  '인문·전통문화',
  '체험·이벤트',
  '취미·여가',
  '미분류',
];

export const cultureQuickCategoryLabels = cultureStandardCategoryLabels.filter((label) => label !== '미분류');

export const educationCategoryLabels = [
  '공공강좌',
  '주민자치',
  '평생학습',
  '정보화교육',
  '교육/강좌',
  '교육·강좌',
  '시민교육',
  '자치회관',
];

export const experienceCategoryLabels = [
  '체험',
  '프로그램',
  '행사',
  '전시',
  '공연',
  '도서관',
  '박물관',
  '과학관',
  '체험행사',
  '자연·생태',
  '견학/야외',
  '예술/공연',
];

export function quickCategoryLabelsForMode(mapMode: CategoryMode) {
  if (mapMode === 'provider') return cultureQuickCategoryLabels;
  if (mapMode === 'education') return educationCategoryLabels;
  return experienceCategoryLabels;
}

export function defaultCategoryLabelsForMode(mapMode: CategoryMode) {
  if (mapMode === 'provider') return cultureStandardCategoryLabels;
  if (mapMode === 'education') return educationCategoryLabels;
  return experienceCategoryLabels;
}

export const dashboardCategoryChipLimit = 8;
export const mobileCategoryChipLimit = 3;

export function priorityCategoryLabelsForMode(mapMode: CategoryMode, limit = dashboardCategoryChipLimit) {
  return quickCategoryLabelsForMode(mapMode).slice(0, limit);
}

export const categoryFilterAliases: Record<string, string[]> = {
  '영유아·놀이': ['영유아', '유아', '오감놀이', 'Kids & Children(event)', 'Kids & Children', 'Kids', 'With mom(event)', 'With Mom', 'Music & Play', 'TODDLER', 'CHILD', 'INFANT', '엄마랑 아가랑', '엄마랑아기랑', '키즈강좌', '유아강좌', '아동강좌', '초등강좌', '영아강좌'],
  '미술·공예': ['미술', 'Art', 'Arts', 'Drawing', 'Crafts', 'Beauty & Design', '문화예술교육', '미술/서예', '공예·전통'],
  '음악·악기': ['음악', 'Music', 'Music & Play', '노래/댄스', '연기·표현'],
  '무용·댄스·운동': ['체육', '체육/스포츠', 'Dance & Exercise', 'Fitness', '생활체육', '건강체력', '건강/뷰티', '건강교실 > 요가.필라테스', '스포츠'],
  '요리·베이킹': ['요리', 'Home Cook', 'Dessert & Beverages', '요리/베이킹'],
  '어학·독서': ['어학', 'Language', '어학/인문', '인문교양교육', '도서관'],
  '과학·창의': ['과학', '코딩', 'Science', '구민정보화교육', '정보화교육', '온라인강좌접수', '로봇', 'AI'],
  '디지털·사진': ['사진·영상', '사진', '영상', '미디어', '컴퓨터', '스마트폰', 'IT', '디지털'],
  '뷰티·생활': ['뷰티', '건강/뷰티', '뷰티/바디', '건강', '힐링'],
  '재테크·경제': ['재테크', '경제', '금융', '부동산'],
  '자격·전문': ['자격', '자격증', '진로·직업', '창업', '전문가', '강사'],
  '인문·전통문화': ['인문', '전통', '공예·전통', '역사', '문화유산'],
  '체험·이벤트': ['체험', '이벤트', '체험행사', '체험·견학', '체험/견학', '교육체험', '원데이', '1일특강', '단기'],
  '취미·여가': ['취미', '바둑', '보드게임', '마술'],

  영유아: ['영유아·놀이', '유아', '오감놀이', 'Kids & Children(event)', 'Kids & Children', 'Kids', 'With mom(event)', 'With Mom', 'Music & Play', 'TODDLER', 'CHILD', 'INFANT'],
  유아: ['영유아·놀이', '영유아', 'Kids & Children(event)', 'Kids & Children', 'Kids', 'With mom(event)', 'With Mom', 'TODDLER', 'CHILD', 'INFANT'],
  초등: ['영유아·놀이', 'Kids & Children(event)', 'Kids & Children', 'Kids', 'CHILD'],
  오감놀이: ['영유아·놀이', '영유아', 'With mom(event)', 'With Mom', 'Music & Play'],
  미술: ['미술·공예', 'Art', 'Arts', 'Drawing', 'Crafts', 'Beauty & Design', '문화예술교육', '미술/서예'],
  음악: ['음악·악기', 'Music', 'Music & Play', '노래/댄스'],
  체육: ['무용·댄스·운동', '체육/스포츠', 'Dance & Exercise', 'Fitness', '생활체육', '건강체력', '건강/뷰티', '건강교실 > 요가.필라테스'],
  요리: ['요리·베이킹', 'Home Cook', 'Dessert & Beverages', '요리/베이킹'],
  어학: ['어학·독서', 'Language', '어학/인문', '인문교양교육'],
  과학: ['과학·창의', '과학관', '박물관/과학관'],
  코딩: ['과학·창의', '디지털·사진', '구민정보화교육', '정보화교육', '온라인강좌접수'],
  체험: ['체험·이벤트', '도서관', '박물관', '과학관', '미술관', '문화재단', '박물관/과학관', '예술/공연', '교육체험', '체험·견학', '체험/견학', '체험행사'],
  공공강좌: ['공공예약', '교육/강좌', '교육·강좌', '평생학습', '평생교육', '평생교육/공공예약', '통합예약', '지자체', '시청', '구청', '군청', '주민자치', '주민자치센터', '주민센터', '행정복지센터'],
  도서관: ['도서관'],
  박물관: ['박물관/과학관', '미술관'],
  과학관: ['박물관/과학관', '과학'],
  체험행사: ['체험·이벤트', '체험·견학', '체험/견학', '교육체험'],
  원데이: ['체험·이벤트', '1일특강', '단기'],
  복지관: ['복지관'],
  주민자치: ['주민자치센터', '주민자치회', '주민센터', '자치회관'],
  정보화교육: ['구민정보화교육', '시민정보화교육', '컴퓨터교육', '디지털교육'],
  시민교육: ['시민교육', '구민교육', '주민교육'],
  자치회관: ['주민자치', '주민자치센터', '주민자치회'],
  '자연·생태': ['수목원/생태', '생태'],
};

function uniqueValues(values: string[]) {
  return Array.from(new Set(values));
}

export function expandCategoryFilterValues(values: string[], includeAliases = true) {
  if (!includeAliases) return uniqueValues(values);
  return uniqueValues(values.flatMap((value) => [value, ...(categoryFilterAliases[value] || [])]));
}

export function categoryFilterValuesForMode(values: string[], mapMode: CategoryMode) {
  return expandCategoryFilterValues(values, mapMode !== 'provider');
}
