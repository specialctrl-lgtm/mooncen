import { describe, expect, it } from 'vitest';
import type { ClassItem } from '../data/mockData';
import {
  applyStatusFilter,
  buildAgeFilterOptions,
  defaultStatusFilters,
  expandedStatusFilters,
  inferTimeBuckets,
  interleaveItemsByBranch,
  matchesChildAge,
  matchesCourseDate,
  parseMonthRange,
  toggleSpecificFilterValue,
} from './courseFilters';

function makeItem(overrides: Partial<ClassItem> = {}): ClassItem {
  return {
    id: 'course-1',
    title: '오감 놀이',
    age: '12~24개월',
    ageGroup: 'TODDLER',
    ageFilter: '유아',
    category: '놀이',
    categoryValues: ['놀이'],
    instructor: '강사',
    schedule: '수 10:30',
    scheduleDate: '2026.07.10',
    scheduleTime: '10:30',
    scheduleDays: ['수'],
    scheduleDates: [],
    center: '테스트 지점',
    branchId: 'branch-a',
    provider: 'LOTTE',
    providerLabel: '롯데',
    price: 10000,
    materialFee: 0,
    status: '접수중',
    statusCode: 'OPEN',
    source: 'L',
    thumbnailClass: 'mint',
    thumbnailEmoji: '🎨',
    aiTags: [],
    tags: [],
    ...overrides,
  };
}

describe('course filter helpers', () => {
  it('defaults to accepting courses while retaining the expanded non-closed status set', () => {
    expect(defaultStatusFilters).toEqual(['OPEN', 'DEADLINE']);
    expect(expandedStatusFilters).toEqual(['OPEN', 'SCHEDULED', 'DEADLINE', 'WAITING']);
  });

  it('switches from all values to one value and restores all when the last value is removed', () => {
    const all = ['월', '화', '수'];

    expect(toggleSpecificFilterValue(all, '화', all)).toEqual(['화']);
    expect(toggleSpecificFilterValue(['화'], '화', all)).toEqual(all);
  });

  it('keeps every age option while a filtered response only contains the selected age', () => {
    const options = buildAgeFilterOptions(['성인'], ['성인']);
    const values = options.map((option) => option.value);

    expect(values).toEqual([
      '영아',
      '유아',
      '아동',
      '청소년',
      '성인',
      '시니어',
      '전체',
      '연령 미정',
    ]);
    expect(options.find((option) => option.value === '전체')?.label).toBe('전연령');
    expect(options.filter((option) => ['영아', '유아', '아동'].includes(option.value))).toEqual([
      { value: '영아', label: '영아', hint: '0~23개월' },
      { value: '유아', label: '유아', hint: '만 2~6세' },
      { value: '아동', label: '아동', hint: '만 7~13세' },
    ]);
  });

  it('normalizes status, time, and date matching', () => {
    const item = makeItem({ schedule: '수요일 오후 19:30', scheduleDates: ['2026-07-10'] });

    expect(applyStatusFilter([item], ['OPEN'])).toEqual([item]);
    expect(inferTimeBuckets(item)).toEqual(expect.arrayContaining(['afternoon', 'evening']));
    expect(matchesCourseDate(item, '2026-07-10')).toBe(true);
    expect(matchesCourseDate(item, '2026-07-11')).toBe(false);
  });

  it('parses month ranges and applies the submitted child age', () => {
    expect(parseMonthRange('대상 12개월~만 3세')).toEqual({ min: 12, max: 36 });
    expect(matchesChildAge(makeItem(), '18', '')).toBe(true);
    expect(matchesChildAge(makeItem(), '36', '')).toBe(false);
  });

  it('interleaves branches without changing order inside each branch', () => {
    const items = [
      makeItem({ id: 'a-1', branchId: 'a' }),
      makeItem({ id: 'a-2', branchId: 'a' }),
      makeItem({ id: 'b-1', branchId: 'b' }),
      makeItem({ id: 'b-2', branchId: 'b' }),
    ];

    expect(interleaveItemsByBranch(items).map((item) => item.id)).toEqual(['a-1', 'b-1', 'a-2', 'b-2']);
  });
});
