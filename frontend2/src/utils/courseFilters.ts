import type { ClassItem } from '../data/mockData';

export type BranchSubFilters = {
  age?: string;
  date?: string;
  day?: string;
  time?: string;
};

export const MAX_COMPARE_ITEMS = 4;
export const allFees = ['free', 'under50000', 'under100000', 'over100000'];
export const allStatuses = ['OPEN', 'SCHEDULED', 'DEADLINE', 'WAITING', 'CLOSED'];
export const defaultStatusFilters = ['OPEN', 'DEADLINE'];
export const expandedStatusFilters = ['OPEN', 'SCHEDULED', 'DEADLINE', 'WAITING'];
export const allDays = ['월', '화', '수', '목', '금', '토', '일', '요일 미정'];
export const apiDays = allDays;
export const allTimes = ['morning', 'afternoon', 'evening', 'time_unknown'];
export const defaultAgeFilterValues = [
  '영아',
  '유아',
  '아동',
  '청소년',
  '성인',
  '시니어',
  '전체',
  '연령 미정',
];
export const quickTimeLabels: Record<string, string> = {
  morning: '오전',
  afternoon: '오후',
  evening: '저녁',
  time_unknown: '시간 미정',
};

const ageFilterAliases: Record<string, string[]> = {
  영아: ['INFANT'],
  유아: ['TODDLER'],
  아동: ['CHILD'],
  청소년: ['TEEN'],
  성인: ['ADULT'],
  시니어: ['SENIOR'],
  전체: ['ALL'],
  전연령: ['ALL'],
  영유아: ['INFANT', 'TODDLER', 'CHILD', '영아', '유아', '아동'],
  '연령 미정': ['UNKNOWN'],
};

const ageFilterHints: Record<string, string> = {
  영아: '0~23개월',
  유아: '만 2~6세',
  아동: '만 7~13세',
};

export function expandAgeFilterValues(values: string[]) {
  return Array.from(new Set(values.flatMap((value) => [value, ...(ageFilterAliases[value] || [])])));
}

export const debugMode = (() => {
  const value = new URLSearchParams(window.location.search).get('debug')?.toLowerCase();
  return value === '1' || value === 'true' || value === 'yes';
})();
export const COURSE_PAGE_SIZE = debugMode ? 120 : 40;

export function applyStatusFilter(items: ClassItem[], statusFilters: string[]) {
  if (!statusFilters.length) return [];
  const target: Record<string, string> = {
    OPEN: '접수중',
    SCHEDULED: '접수예정',
    DEADLINE: '마감임박',
    WAITING: '대기접수',
    CLOSED: '마감',
  };
  return items.filter((item) => {
    const statusCode = (item.statusCode || '').toUpperCase();
    if (statusCode) return statusFilters.includes(statusCode);
    return statusFilters.some((statusFilter) => item.status.trim() === (target[statusFilter] ?? statusFilter));
  });
}

export function normalizedScheduleDays(item: ClassItem) {
  const days = item.scheduleDays?.length ? item.scheduleDays : [];
  return days.length ? days : ['요일 미정'];
}

export function inferTimeBuckets(item: ClassItem) {
  const text = item.schedule || '';
  const buckets = new Set<string>();
  if (/오전|아침/.test(text)) buckets.add('morning');
  if (/오후|낮/.test(text)) buckets.add('afternoon');
  if (/저녁|야간|밤/.test(text)) buckets.add('evening');

  const matches = text.matchAll(/(\d{1,2})(?::|시)\s*(\d{2})?/g);
  for (const match of matches) {
    const hour = Number(match[1]);
    if (Number.isNaN(hour)) continue;
    if (hour < 12) buckets.add('morning');
    else if (hour < 18) buckets.add('afternoon');
    else buckets.add('evening');
  }

  return buckets.size ? [...buckets] : ['time_unknown'];
}

export function applyMultiValueFilter(
  items: ClassItem[],
  selectedValues: string[],
  readValues: (item: ClassItem) => string[],
) {
  if (!selectedValues.length) return [];
  const selected = new Set(selectedValues);
  return items.filter((item) => readValues(item).some((value) => selected.has(value)));
}

export function classItemCategoryValues(item: ClassItem) {
  return item.categoryValues?.length ? item.categoryValues : [item.category].filter(Boolean);
}

export function classItemAgeValues(item: ClassItem) {
  return Array.from(new Set([item.ageGroup, item.targetAgeGroup]
    .map((value) => String(value || '').trim())
    .filter(Boolean)));
}

export function sameStringSet(left: string[], right: string[]) {
  if (left.length !== right.length) return false;
  const rightSet = new Set(right);
  return left.every((value) => rightSet.has(value));
}

export function toggleSpecificFilterValue(current: string[], value: string, allValues: string[]) {
  if (sameStringSet(current, allValues)) {
    return [value];
  }
  const next = current.includes(value)
    ? current.filter((item) => item !== value)
    : [...current, value];
  return next.length ? next : allValues;
}

export function interleaveItemsByBranch(items: ClassItem[]) {
  const buckets = new Map<string, ClassItem[]>();
  const order: string[] = [];

  items.forEach((item) => {
    const key = item.branchId || `${item.provider}:${item.center || 'unknown'}`;
    if (!buckets.has(key)) {
      buckets.set(key, []);
      order.push(key);
    }
    buckets.get(key)?.push(item);
  });

  const mixed: ClassItem[] = [];
  let hasItems = true;
  while (hasItems) {
    hasItems = false;
    order.forEach((key) => {
      const bucket = buckets.get(key);
      const item = bucket?.shift();
      if (item) {
        mixed.push(item);
        hasItems = true;
      }
    });
  }

  return mixed;
}

export function buildFilterOptions(values: string[]) {
  return [...new Set(values.filter(Boolean))]
    .sort((a, b) => a.localeCompare(b, 'ko-KR'))
    .map((value) => ({ value, label: displayFilterLabel(value) }));
}

export function buildAgeFilterOptions(observedValues: string[], selectedValues: string[] = []) {
  const defaultSet = new Set(defaultAgeFilterValues);
  const extraValues = [...new Set([...observedValues, ...selectedValues].filter(Boolean))]
    .filter((value) => !defaultSet.has(value))
    .sort((left, right) => left.localeCompare(right, 'ko-KR'));

  return [...defaultAgeFilterValues, ...extraValues]
    .map((value) => {
      const label = ageFilterLabel(value);
      const hint = ageFilterHints[value];
      return hint ? { value, label, hint } : { value, label };
    });
}

export function displayFilterLabel(value: string) {
  const text = String(value || '').trim();
  if (!text) return '미분류';
  if (
    /[?�]/.test(text) ||
    /(誘몃텇|湲고|臾명|援먯|泥댄|怨듦|꾩|좎|먯|쇳|뺤|쥖|솕|瑜)/.test(text)
  ) {
    return '미분류';
  }
  return text;
}

export function ageFilterLabel(value: string) {
  return value === '전체' ? '전연령' : value;
}

export function parseMonthRange(text: string) {
  const mixedRange = text.match(/(\d{1,3})\s*개월\s*[~-]\s*(?:만\s*)?(\d{1,2})\s*세/);
  if (mixedRange) return { min: Number(mixedRange[1]), max: Number(mixedRange[2]) * 12 };
  const repeatedUnitRange = text.match(/(\d{1,3})\s*개월\s*[~-]\s*(\d{1,3})\s*개월/);
  if (repeatedUnitRange) return { min: Number(repeatedUnitRange[1]), max: Number(repeatedUnitRange[2]) };
  const range = text.match(/(\d{1,3})\s*[~-]\s*(\d{1,3})\s*개월/);
  if (range) return { min: Number(range[1]), max: Number(range[2]) };
  const single = text.match(/(\d{1,3})\s*개월/);
  if (single) return { min: Number(single[1]), max: Number(single[1]) };
  return null;
}

function compatibleAgeGroupsForMonths(months: number) {
  if (months < 36) return new Set(['INFANT', 'TODDLER', '영아', '유아']);
  if (months < 84) return new Set(['TODDLER', 'CHILD', '유아', '아동']);
  if (months < 156) return new Set(['CHILD', '아동']);
  if (months < 216) return new Set(['TEEN', '청소년']);
  return new Set(['ADULT', 'SENIOR', '성인', '시니어']);
}

export function matchesChildAge(item: ClassItem, childAgeMonths: string, childAgeYears: string) {
  const monthValue = childAgeMonths ? Number(childAgeMonths) : childAgeYears ? Number(childAgeYears) * 12 : NaN;
  if (Number.isNaN(monthValue)) return true;

  const monthRange = parseMonthRange(`${item.age} ${item.title}`);
  if (monthRange) return monthValue >= monthRange.min && monthValue <= monthRange.max;

  const minAge = item.targetMinAge;
  const maxAge = item.targetMaxAge;
  const ageText = `${item.age} ${item.title}`;
  const numericValuesAreMonths =
    /개월/.test(ageText) ||
    (minAge != null && minAge > 24) ||
    (maxAge != null && maxAge > 24);
  const minMonths = minAge == null ? null : numericValuesAreMonths ? minAge : minAge * 12;
  const maxMonths = maxAge == null ? null : numericValuesAreMonths ? maxAge : maxAge * 12;
  if (minMonths != null && maxMonths != null) return monthValue >= minMonths && monthValue <= maxMonths;
  if (minMonths != null) return monthValue >= minMonths;
  if (maxMonths != null) return monthValue <= maxMonths;
  const compatibleGroups = compatibleAgeGroupsForMonths(monthValue);
  return classItemAgeValues(item).some((value) => compatibleGroups.has(value));
}

export function matchesCourseDate(item: ClassItem, selectedDateValue?: string) {
  if (!selectedDateValue) return true;
  if (item.scheduleDates.length) return item.scheduleDates.includes(selectedDateValue);
  if (item.startDate && item.endDate) return item.startDate <= selectedDateValue && selectedDateValue <= item.endDate;
  if (item.startDate) return item.startDate === selectedDateValue;
  if (item.endDate) return item.endDate === selectedDateValue;
  return item.scheduleDate.includes(selectedDateValue) || item.scheduleDate.includes(selectedDateValue.replace(/-/g, '.'));
}

export function matchesBranchSubFilters(item: ClassItem, filters: BranchSubFilters) {
  if (filters.age && item.ageGroup !== filters.age) return false;
  if (filters.date && !matchesCourseDate(item, filters.date)) return false;
  if (filters.day && !normalizedScheduleDays(item).includes(filters.day)) return false;
  if (filters.time && !inferTimeBuckets(item).includes(filters.time)) return false;
  return true;
}

export function branchSubFilterActive(filters: BranchSubFilters) {
  return Boolean(filters.age || filters.date || filters.day || filters.time);
}
