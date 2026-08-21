import { describe, expect, it } from 'vitest';

import type { ClassItem } from '../data/mockData';
import { sortCourseItems } from './courseSort';


function course(id: string, overrides: Partial<ClassItem> = {}): ClassItem {
  return {
    id,
    title: id,
    age: '',
    ageGroup: '',
    ageFilter: '',
    category: '',
    categoryValues: [],
    instructor: '',
    schedule: '',
    scheduleDate: '',
    scheduleTime: '',
    scheduleDays: [],
    scheduleDates: [],
    center: '',
    provider: '',
    providerLabel: '',
    price: 0,
    materialFee: 0,
    status: '접수중',
    statusCode: 'OPEN',
    source: 'P',
    thumbnailClass: '',
    thumbnailEmoji: '',
    aiTags: [],
    tags: [],
    ...overrides,
  };
}

describe('sortCourseItems', () => {
  it('uses real popularity signals and keeps the input immutable', () => {
    const original = [course('low', { viewCount: 5 }), course('high', { viewCount: 20 })];
    expect(sortCourseItems(original, 'popular').map((item) => item.id)).toEqual(['high', 'low']);
    expect(original.map((item) => item.id)).toEqual(['low', 'high']);
  });

  it('sorts latest by server timestamps', () => {
    const items = [
      course('old', { createdAt: '2026-01-01T00:00:00Z' }),
      course('new', { createdAt: '2026-07-01T00:00:00Z' }),
    ];
    expect(sortCourseItems(items, 'latest').map((item) => item.id)).toEqual(['new', 'old']);
  });

  it('puts the nearest actionable deadline first', () => {
    const now = Date.parse('2026-07-10T00:00:00Z');
    const items = [
      course('closed', { status: '마감', statusCode: 'CLOSED', applyEnd: '2026-07-11T00:00:00Z' }),
      course('later', { applyEnd: '2026-07-20T00:00:00Z' }),
      course('soon', { applyEnd: '2026-07-12T00:00:00Z' }),
    ];
    expect(sortCourseItems(items, 'deadline', now).map((item) => item.id)).toEqual(['soon', 'later', 'closed']);
  });
});
