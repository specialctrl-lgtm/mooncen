import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ClassItem } from '../data/mockData';
import { formatCoursePrice } from '../utils/courseCardDisplay';
import ClassCard from './ClassCard';

const item: ClassItem = {
  id: 'course-1',
  title: '그리너리 꽃다발',
  age: '만 4~6세',
  ageGroup: '유아',
  ageFilter: '만 4~6세',
  category: '미술·공예',
  categoryValues: ['미술·공예'],
  instructor: '강사 미정',
  schedule: '2026-08-30 13:30~14:50',
  scheduleDate: '8.30',
  scheduleTime: '13:30~14:50',
  scheduleDays: ['일'],
  scheduleDates: ['2026-08-30'],
  sessions: 1,
  center: '이마트 문화센터 흥덕점',
  provider: 'EMART',
  providerLabel: '이마트 문화센터',
  price: 40000,
  priceKnown: true,
  materialFee: 0,
  status: '접수중',
  statusLabel: '접수중',
  statusCode: 'OPEN',
  source: 'E',
  thumbnailClass: 'thumb-mint',
  thumbnailEmoji: '꽃',
  applicationUrl: 'https://example.com/apply',
  aiTags: [],
  tags: [],
  startDate: '2026-08-30',
  endDate: '2026-08-30',
  applyStart: '2026-04-23',
  applyEnd: '2026-08-29',
  distanceKm: 1.2,
};

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-07-27T12:00:00+09:00'));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('ClassCard', () => {
  it('renders the comparison-first three-row summary', () => {
    const onToggleCompare = vi.fn();
    const { container } = render(
      <ClassCard
        item={item}
        isFavorite={false}
        isCompared={false}
        onToggleFavorite={vi.fn()}
        onApply={vi.fn()}
        onToggleCompare={onToggleCompare}
        onOpenDetails={vi.fn()}
        onOpenLocation={vi.fn()}
      />,
    );

    expect(screen.getByText('8.30(일) 13:30~14:50 · 총 1회')).toBeTruthy();
    expect(screen.getByText('4.23(목) ~ 8.29(토)')).toBeTruthy();
    expect(screen.getByText('이마트 문화센터 흥덕점 · 1.2km')).toBeTruthy();
    expect(screen.getByText('40,000원')).toBeTruthy();
    expect(screen.queryByText('강사 미정')).toBeNull();
    expect(screen.getByRole('button', { name: /신청 페이지 새 창으로 열기/ })).toBeTruthy();
    const statusBadge = container.querySelector('.course-card-thumbnail > .status-badge.status-open');
    expect(statusBadge?.textContent).toBe('접수중');
    expect(container.querySelector('.course-card-overview > .course-price')).toBeTruthy();
    expect(container.querySelector('.course-card-heading .course-price')).toBeNull();

    fireEvent.click(screen.getByRole('checkbox', { name: '비교 담기' }));
    expect(onToggleCompare).toHaveBeenCalledWith(item);
  });

  it('distinguishes free courses from missing price data', () => {
    expect(formatCoursePrice({ ...item, price: 0, priceKnown: true })).toBe('무료');
    expect(formatCoursePrice({ ...item, price: 0, priceKnown: false })).toBe('수강료 확인');
  });

  it('renders material fees in their own fixed price row', () => {
    const { container } = render(
      <ClassCard
        item={{ ...item, materialFee: 6000 }}
        isFavorite={false}
        isCompared={false}
        onToggleFavorite={vi.fn()}
        onApply={vi.fn()}
        onToggleCompare={vi.fn()}
        onOpenDetails={vi.fn()}
        onOpenLocation={vi.fn()}
      />,
    );

    const price = container.querySelector('.course-price');
    expect(price?.querySelector('strong')?.textContent).toBe('40,000원');
    expect(price?.querySelector('.course-material-fee')?.textContent).toBe('재료비 6,000원');
  });

  it('shows the exact municipal branch without the collection scope label', () => {
    render(
      <ClassCard
        item={{
          ...item,
          center: '영덕도서관',
          provider: 'MUNI_LIB_YONGIN_GO_KR_B7626320',
          providerLabel: '용인시도서관 22개관 전체 교육강좌',
        }}
        isFavorite={false}
        isCompared={false}
        onToggleFavorite={vi.fn()}
        onApply={vi.fn()}
        onToggleCompare={vi.fn()}
        onOpenDetails={vi.fn()}
        onOpenLocation={vi.fn()}
      />,
    );

    expect(screen.getByText('영덕도서관 · 1.2km')).toBeTruthy();
    expect(screen.queryByText(/용인시도서관 22개관 전체 교육강좌 영덕도서관/)).toBeNull();
  });

  it('shows the physical venue for a non-MUNI public-course provider', () => {
    render(
      <ClassCard
        item={{
          ...item,
          center: '부국원 3층 교육실',
          provider: 'SUWON_RESERV_EDUCATION',
          providerLabel: '수원시 통합예약',
          serviceGroup: '공공강좌',
        }}
        isFavorite={false}
        isCompared={false}
        onToggleFavorite={vi.fn()}
        onApply={vi.fn()}
        onToggleCompare={vi.fn()}
        onOpenDetails={vi.fn()}
        onOpenLocation={vi.fn()}
      />,
    );

    expect(screen.getByText('부국원 3층 교육실 · 1.2km')).toBeTruthy();
    expect(screen.queryByText(/수원시 통합예약 부국원/)).toBeNull();
  });

  it('opens the location popup from the location row', () => {
    const onOpenLocation = vi.fn();
    render(
      <ClassCard
        item={item}
        isFavorite={false}
        isCompared={false}
        onToggleFavorite={vi.fn()}
        onApply={vi.fn()}
        onToggleCompare={vi.fn()}
        onOpenDetails={vi.fn()}
        onOpenLocation={onOpenLocation}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /지도에서 보기/ }));

    expect(onOpenLocation).toHaveBeenCalledWith(item);
  });
});
