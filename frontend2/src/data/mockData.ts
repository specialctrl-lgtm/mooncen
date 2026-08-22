import type { Branch, Course } from '../api';
import { branchDisplayName, cultureProviderLabel } from '../utils/branchDisplay';
import { normalizeCourseDisplayTitle } from '../utils/titleDisplay';

export type Category = {
  id: string;
  name: string;
  icon: string;
};

export type SourceCode = 'H' | 'L' | 'E' | 'HD' | 'S' | 'ER' | 'A' | 'G' | 'M' | 'P';

export type ClassItem = {
  id: string;
  title: string;
  age: string;
  ageGroup: string;
  ageFilter: string;
  targetAgeGroup?: string | null;
  targetMinAge?: number | null;
  targetMaxAge?: number | null;
  serviceGroup?: string | null;
  programType?: string | null;
  category: string;
  categoryValues: string[];
  instructor: string;
  schedule: string;
  scheduleDate: string;
  scheduleTime: string;
  scheduleRaw?: string | null;
  scheduleDays: string[];
  scheduleDates: string[];
  sessionLabel?: string | null;
  sessions?: number | null;
  center: string;
  venueName?: string | null;
  venueAddress?: string | null;
  branch?: Branch | null;
  branchId?: string | null;
  provider: string;
  providerCourseId?: string | null;
  providerLabel: string;
  price: number;
  priceKnown?: boolean;
  materialFee: number;
  distanceKm?: number | null;
  status: string;
  statusLabel?: string | null;
  statusCode?: string | null;
  source: SourceCode;
  thumbnailClass: string;
  thumbnailEmoji: string;
  imageUrl?: string | null;
  applicationUrl?: string | null;
  rawUrl?: string | null;
  description?: string | null;
  aiSummary?: string | null;
  aiTags: string[];
  tags: string[];
  startDate?: string | null;
  endDate?: string | null;
  applyStart?: string | null;
  applyEnd?: string | null;
  applyPeriodRaw?: string | null;
  capacityTotal?: number | null;
  capacityCurrent?: number | null;
  capacityRemaining?: number | null;
  waitlistTotal?: number | null;
  eligibilityRaw?: string | null;
  reservationAvailable?: boolean | null;
  popularityScore?: number | null;
  favoriteCount?: number | null;
  viewCount?: number | null;
  createdAt?: string | null;
  updatedAt?: string | null;
};

export type PopularClassItem = ClassItem & {
  rank: number;
};

export type MapPin = {
  id: string;
  label: string;
  source: SourceCode;
  color: 'mint' | 'coral' | 'yellow' | 'blue' | 'purple' | 'green' | 'black';
  x: number;
  y: number;
};

export const categories: Category[] = [
  { id: 'all', name: '전체', icon: 'grid' },
  { id: '영유아', name: '영유아', icon: 'baby' },
  { id: '미술', name: '미술', icon: 'palette' },
  { id: '음악', name: '음악', icon: 'music' },
  { id: '체육', name: '체육', icon: 'activity' },
  { id: '요리', name: '요리', icon: 'chef' },
  { id: '어학', name: '어학', icon: 'book' },
  { id: '과학', name: '과학', icon: 'flask' },
  { id: '코딩', name: '코딩', icon: 'code' },
];

const providerSource: Record<string, SourceCode> = {
  HOMEPLUS: 'H',
  LOTTE: 'L',
  EMART: 'E',
  HYUNDAI_DEPT: 'HD',
  SHINSEGAE_ACADEMY: 'S',
  ELAND_RETAIL: 'ER',
  AK_PLAZA: 'A',
  GALLERIA: 'G',
  LOTTE_MART: 'M',
};

const sourceColor: Record<SourceCode, MapPin['color']> = {
  H: 'mint',
  L: 'coral',
  E: 'yellow',
  HD: 'green',
  S: 'mint',
  ER: 'black',
  A: 'blue',
  G: 'black',
  M: 'coral',
  P: 'mint',
};

function normalizeStatus(status?: string | null) {
  if (!status) return '상태 미정';
  const labels: Record<string, string> = {
    OPEN: '접수중',
    SCHEDULED: '접수예정',
    CLOSED: '마감',
    WAITING: '대기접수',
    DEADLINE: '마감임박',
  };
  return labels[status] ?? status;
}

function normalizeAgeGroup(ageGroup?: string | null) {
  const labels: Record<string, string> = {
    INFANT: '영아',
    TODDLER: '유아',
    CHILD: '아동',
    TEEN: '청소년',
    ADULT: '성인',
    SENIOR: '시니어',
    ALL: '전체',
  };
  return ageGroup ? labels[ageGroup] ?? ageGroup : '연령 미정';
}

function monthToLabel(month: number) {
  if (month >= 48) {
    const years = Math.floor(month / 12);
    return `만 ${years}세`;
  }
  return `${month}개월`;
}

function monthRangeToLabel(min: number, max: number) {
  if (min < 48 || max < 48 || min % 12 !== 0 || (max % 12 !== 0 && max % 12 !== 11)) {
    return `${min}~${max}개월`;
  }

  const minYears = Math.floor(min / 12);
  const maxYears = Math.floor(max / 12);
  return minYears === maxYears ? `만 ${minYears}세` : `만 ${minYears}~${maxYears}세`;
}

function formatTargetAgeRange(minAge?: number | null, maxAge?: number | null) {
  const min = minAge != null && minAge > 0 ? minAge : null;
  const max = maxAge != null && maxAge > 0 ? maxAge : null;
  if (min == null && max == null) return null;
  if (min != null && max != null) {
    return min === max ? monthToLabel(min) : monthRangeToLabel(min, max);
  }
  if (min != null) return `${monthToLabel(min)} 이상`;
  return `${monthToLabel(max!)} 이하`;
}

function normalizeBirthYear(value: string, referenceYear?: number) {
  const year = Number(value);
  if (year >= 1000) return year;
  if (referenceYear && referenceYear >= 1000) {
    return Math.floor(referenceYear / 100) * 100 + year;
  }
  return 2000 + year;
}

function birthYearToAgeLabel(value: string) {
  const age = new Date().getFullYear() - normalizeBirthYear(value);
  return `만 ${Math.max(0, age)}세`;
}

function normalizeSpecificAgeText(value: string) {
  return value
    .replace(/^\s*대상\s*[:：]?\s*/, '')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/(\d{2,4})\s*[~-]\s*(\d{2,4})\s*년생/g, (_match, startRaw: string, endRaw: string) => {
      let startYear = normalizeBirthYear(startRaw);
      let endYear = normalizeBirthYear(endRaw, startYear);
      if (endYear < startYear) [startYear, endYear] = [endYear, startYear];
      const ages = [new Date().getFullYear() - startYear, new Date().getFullYear() - endYear].sort((a, b) => a - b);
      return ages[0] === ages[1] ? `만 ${ages[0]}세` : `만 ${ages[0]}~${ages[1]}세`;
    })
    .replace(/(\d{2,4})\s*년생(?:\s*(이상|이하|부터|까지))?/g, (_match, yearRaw: string, suffix?: string) => {
      return `${birthYearToAgeLabel(yearRaw)}${suffix ? ` ${suffix}` : ''}`;
    })
    .replace(/(\d{1,3})\s*개월\s*[~-]\s*(?:만\s*)?(\d{1,2})\s*세/g, (_match, startRaw: string, endYearRaw: string) => {
      return monthRangeToLabel(Number(startRaw), Number(endYearRaw) * 12);
    })
    .replace(/(\d{1,3})\s*[~-]\s*(\d{1,3})\s*개월/g, (_match, startRaw: string, endRaw: string) => {
      return monthRangeToLabel(Number(startRaw), Number(endRaw));
    })
    .replace(/(\d{1,3})\s*개월\s*(이상|이하|부터|까지)?/g, (_match, monthRaw: string, suffix?: string) => {
      return `${monthToLabel(Number(monthRaw))}${suffix ? ` ${suffix}` : ''}`;
    })
    .replace(/\s*([~-])\s*/g, '$1')
    .replace(/\s*,\s*/g, ', ')
    .trim();
}

function extractSpecificAgeText(target?: string | null) {
  if (!target) return null;
  const text = normalizeSpecificAgeText(target);
  const patterns = [
    /만\s*\d{1,2}\s*[~-]\s*\d{1,2}\s*세/g,
    /만\s*\d{1,2}\s*세\s*(?:이상|이하|부터|까지)?/g,
    /\d{4}\s*[~-]\s*\d{2,4}\s*년생/g,
    /\d{2}\s*[~-]\s*\d{2}\s*년생/g,
    /\d{1,3}\s*[~-]\s*\d{1,3}\s*개월/g,
    /\d{1,3}\s*개월\s*(?:이상|이하|부터|까지)?/g,
  ];
  const matches = patterns.flatMap((pattern) => text.match(pattern) ?? []);
  const unique = Array.from(new Set(matches.map(normalizeSpecificAgeText)));
  return unique.length ? unique.join(', ') : null;
}

function targetDisplay(course: Course) {
  const ageGroup = normalizeAgeGroup(course.target_age_group);
  if (course.target_age_group === 'ADULT') return ageGroup;
  const range = formatTargetAgeRange(course.target_min_age, course.target_max_age);
  const specific = extractSpecificAgeText(course.target);
  const parts = [ageGroup, range || specific].filter(Boolean);
  return parts.length ? Array.from(new Set(parts)).join(' · ') : '연령 미정';
}

function serviceCategory(course: Course) {
  const value = String(course.service_group || '').trim();
  return value === '체험' || value === '공공강좌' ? value : '';
}

const cultureCenterProviders = new Set([
  'HOMEPLUS',
  'LOTTE',
  'EMART',
  'HYUNDAI_DEPT',
  'GALLERIA',
  'AK_PLAZA',
  'ELAND_RETAIL',
  'SHINSEGAE_ACADEMY',
  'LOTTE_MART',
]);
const emptySubjectCategoryNames = new Set(['미분류', 'uncategorized', 'Other']);

function categoryText(value?: string | null) {
  return String(value || '').trim();
}

function usefulSubjectCategory(value?: string | null) {
  const text = categoryText(value);
  return text && !emptySubjectCategoryNames.has(text) ? text : '';
}

function isCultureCenterCourse(course: Course) {
  return cultureCenterProviders.has(course.provider);
}

function normalizeCategory(course: Course) {
  const standardCategory = usefulSubjectCategory(course.standard_category);
  const aiCategory = usefulSubjectCategory(course.ai_category);
  const sourceCategory = usefulSubjectCategory(course.category_raw);
  if (isCultureCenterCourse(course)) {
    return standardCategory || aiCategory || sourceCategory || usefulSubjectCategory(course.source_group) || '미분류';
  }

  return (
    serviceCategory(course) ||
    standardCategory ||
    aiCategory ||
    usefulSubjectCategory(course.domain_category) ||
    usefulSubjectCategory(course.collection_category) ||
    sourceCategory ||
    usefulSubjectCategory(course.source_group) ||
    '기타'
  );
}

function categoryValues(course: Course) {
  return Array.from(new Set([
    normalizeCategory(course),
    course.service_group,
    course.standard_category,
    course.ai_category,
    course.program_type,
    course.domain_category,
    course.collection_category,
    course.category_raw,
    course.source_group,
    course.branch?.primary_collection_category,
    ...(course.branch?.collection_categories || []),
  ]
    .map((value) => String(value || '').trim())
    .filter(Boolean)));
}

function thumbnailFor(course: Course) {
  const category = normalizeCategory(course).toLowerCase();
  if (course.image_url) return { className: 'thumb-photo', mark: '이미지' };
  if (category.includes('미술') || category.includes('art')) return { className: 'thumb-art', mark: '미술' };
  if (category.includes('음악') || category.includes('music')) return { className: 'thumb-music', mark: '음악' };
  if (category.includes('요리') || category.includes('cook')) return { className: 'thumb-cook', mark: '요리' };
  if (category.includes('체육') || category.includes('sport')) return { className: 'thumb-sport', mark: '체육' };
  return { className: 'thumb-default', mark: '강좌' };
}

function formatDate(value?: string | null) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString('ko-KR', { month: 'numeric', day: 'numeric' });
}

function formatCourseDateLine(course: Course) {
  const start = formatDate(course.start_date);
  const end = formatDate(course.end_date);
  if (start && end && start !== end) return `${start}~${end}`;
  return start || end || '일정 미정';
}

function extractTimeLine(course: Course) {
  const raw = [course.schedule_summary, course.day_schedule, course.schedule_raw].filter(Boolean).join(' ');
  const match = raw.match(/([01]?\d|2[0-3]):[0-5]\d\s*[~-]\s*([01]?\d|2[0-3]):[0-5]\d/);
  if (match) return match[0].replace(/\s+/g, '');
  return course.day_schedule || course.schedule_raw || '';
}

function displayTitle(course: Course) {
  const title =
    course.ai_title_processed && course.ai_title_result?.clean_title
      ? course.ai_title_result.clean_title
      : course.title || course.title_raw;
  const cleaned = (title || '')
    .replace(/^\s*[*•]?\s*\d{1,2}\s*[./월]\s*\d{1,2}\s*(?:\([월화수목금토일]\))?\s*(?:\d{1,2}:\d{2})?\s*/g, ' ')
    .replace(/^\s*[월화수목금토일]\s*<[^>]*>\s*/g, ' ')
    .replace(/\s*\([^)]*(?:\d{1,3}\s*개월|\d{2,4}\s*[~-]\s*\d{2,4}\s*년생|만\s*\d{1,2}\s*세|\d{1,2}\s*[./월]\s*\d{1,2}|[월화수목금토일]\s*요일)[^)]*\)\s*/g, ' ')
    .replace(/\b(?:[01]?\d|2[0-3]):[0-5]\d\s*(?:[~-]\s*(?:[01]?\d|2[0-3]):[0-5]\d)?\b/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  return normalizeCourseDisplayTitle(cleaned);
}

function normalizeTags(course: Course) {
  return Array.from(
    new Set([...(course.target_tags || []), ...(course.ai_tags || [])].filter((tag): tag is string => Boolean(tag))),
  ).slice(0, 8);
}

export function toClassItem(course: Course): ClassItem {
  const thumbnail = thumbnailFor(course);
  const source = providerSource[course.provider] ?? 'P';
  const displayAge = targetDisplay(course);
  const materialFee = Number(course.material_fee ?? 0);

  return {
    id: course.id,
    title: displayTitle(course),
    age: displayAge,
    ageGroup: normalizeAgeGroup(course.target_age_group),
    ageFilter: displayAge,
    targetAgeGroup: course.target_age_group,
    targetMinAge: course.target_min_age,
    targetMaxAge: course.target_max_age,
    serviceGroup: course.service_group,
    programType: usefulSubjectCategory(course.program_type),
    category: normalizeCategory(course),
    categoryValues: categoryValues(course),
    instructor: course.instructor || '강사 미정',
    schedule: course.schedule_summary || course.day_schedule || course.schedule_raw || '일정 미정',
    scheduleDate: formatCourseDateLine(course),
    scheduleTime: extractTimeLine(course),
    scheduleRaw: course.schedule_raw,
    scheduleDays: course.schedule_days || [],
    scheduleDates: course.schedule_dates || [],
    sessionLabel: course.session_label,
    sessions: course.sessions || course.schedule_dates?.length || undefined,
    center: course.branch ? branchDisplayName(course.branch) : course.provider,
    venueName: course.venue_name,
    venueAddress: course.venue_address,
    branch: course.branch,
    branchId: course.branch_id || course.branch?.id,
    provider: course.provider,
    providerCourseId: course.provider_course_id,
    providerLabel: course.provider_label || cultureProviderLabel(course.provider),
    price: Number(course.fee ?? 0),
    priceKnown: course.fee != null,
    materialFee,
    status: normalizeStatus(course.status),
    statusLabel: course.status_label,
    statusCode: course.status,
    source,
    thumbnailClass: thumbnail.className,
    thumbnailEmoji: thumbnail.mark,
    imageUrl: course.image_url,
    applicationUrl: course.application_url,
    rawUrl: course.raw_url,
    description: course.description,
    aiSummary: course.ai_summary,
    aiTags: course.ai_tags || [],
    tags: normalizeTags(course),
    startDate: course.start_date,
    endDate: course.end_date,
    applyStart: course.apply_start,
    applyEnd: course.apply_end,
    applyPeriodRaw: course.apply_period_raw,
    capacityTotal: course.capacity_total,
    capacityCurrent: course.capacity_current,
    capacityRemaining: course.capacity_remaining,
    waitlistTotal: course.waitlist_total,
    eligibilityRaw: course.eligibility_raw,
    reservationAvailable: course.reservation_available,
    popularityScore: course.popularity_score,
    favoriteCount: course.favorite_count,
    viewCount: course.view_count,
    createdAt: course.created_at,
    updatedAt: course.updated_at,
  };
}

export function toPopularItems(courses: Course[]): PopularClassItem[] {
  return courses
    .slice(0, 8)
    .map((course, index) => ({ ...toClassItem(course), rank: index + 1 }));
}

export function toMapPins(branches: Branch[], courses: Course[]): MapPin[] {
  const courseCountByBranch = new Map<string, number>();
  courses.forEach((course) => {
    const branchId = course.branch_id || course.branch?.id;
    if (!branchId) return;
    courseCountByBranch.set(branchId, (courseCountByBranch.get(branchId) || 0) + 1);
  });

  return branches
    .filter((branch) => branch.lat != null && branch.lon != null)
    .map((branch, index) => {
      const source = providerSource[branch.provider] ?? 'P';
      return {
        id: branch.id,
        label: String(courseCountByBranch.get(branch.id) || branch.active_course_count || branch.course_count || index + 1),
        source,
        color: sourceColor[source],
        x: 0,
        y: 0,
      };
    });
}
