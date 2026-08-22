import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ClassItem } from '../data/mockData';
import CourseDetailModal from './CourseDetailModal';


afterEach(() => cleanup());


function item(applicationUrl: string | null): ClassItem {
  return {
    id: 'course-1',
    title: '테스트 강좌',
    age: '성인',
    ageGroup: '성인',
    ageFilter: 'adult',
    category: '문화',
    categoryValues: ['문화'],
    instructor: '강사',
    schedule: '토 10:00',
    scheduleDate: '2026-08-01',
    scheduleTime: '10:00',
    scheduleDays: ['토'],
    scheduleDates: ['2026-08-01'],
    center: '센터',
    provider: 'TEST',
    providerLabel: '테스트 센터',
    price: 10000,
    materialFee: 0,
    status: 'OPEN',
    source: 'P',
    thumbnailClass: 'test',
    thumbnailEmoji: 'T',
    applicationUrl,
    rawUrl: 'https://example.com/course/detail',
    aiTags: [],
    tags: [],
  };
}


function renderModal(course: ClassItem, onApply = vi.fn()) {
  render(
    <CourseDetailModal
      item={course}
      isFavorite={false}
      isApplied={false}
      onClose={vi.fn()}
      onAddMyCourse={vi.fn()}
      onRemoveMyCourse={vi.fn()}
      onApply={onApply}
      onToggleFavorite={vi.fn()}
    />,
  );
  return onApply;
}


describe('CourseDetailModal application action', () => {
  it('uses the official detail URL when an explicit application URL is absent', () => {
    const onApply = renderModal(item(null));
    const button = screen.getByRole('button', { name: '수강신청' });

    expect((button as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(button);
    expect(onApply).toHaveBeenCalledTimes(1);
  });

  it('prefers an explicit application URL when one is available', () => {
    const onApply = renderModal(item('https://example.com/course/apply'));
    const button = screen.getByRole('button', { name: '수강신청' });

    expect((button as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(button);
    expect(onApply).toHaveBeenCalledTimes(1);
  });
});
