import '@testing-library/jest-dom/vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { opsApi } from '../api';
import { OpsProvider } from '../context';
import QualityPage from './QualityPage';

vi.mock('../api', () => ({
  opsApi: vi.fn(),
}));

const mockedOpsApi = vi.mocked(opsApi);

function renderPage(initialEntry = '/data-quality') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <OpsProvider
        session={{
          user: { id: 'operator-id', email: 'operator@example.test', name: '운영자' },
          role: 'operator',
          environment: 'development',
        }}
      >
        <MemoryRouter initialEntries={[initialEntry]}>
          <Routes>
            <Route path="/data-quality" element={<QualityPage />} />
            <Route path="/content" element={<div>콘텐츠 결과</div>} />
          </Routes>
        </MemoryRouter>
      </OpsProvider>
    </QueryClientProvider>,
  );
}

describe('QualityPage category quality', () => {
  afterEach(cleanup);

  beforeEach(() => {
    mockedOpsApi.mockReset();
    mockedOpsApi.mockImplementation(async (path: string) => {
      if (path === '/quality/summary') {
        return {
          available: true,
          counts: {
            active_courses: 12,
            missing_required: 3,
            duplicate_urls: 0,
            invalid_dates: 0,
            missing_address: 7,
            missing_coordinates: 9,
            incomplete_location: 11,
            blocked_sync: 0,
          },
          issue_statuses: [],
          rule_source: 'production',
        };
      }
      if (path === '/quality/categories?level=major&limit=10') {
        return {
          available: true,
          total: 1,
          items: [
            {
              content_type: 'education',
              category: '교육',
              active_count: 12,
              provider_count: 3,
              field_completeness: 83.3,
              complete_count: 6,
              target_count: 12,
              fee_count: 6,
              date_count: 12,
              place_count: 12,
              category_count: 12,
              time_count: 6,
              encoding_issue_count: 4,
              checked_count: 2,
              good_count: 1,
              warning_count: 1,
              bad_count: 0,
              unchecked_count: 10,
            },
          ],
        };
      }
      if (path === '/quality/providers?content_type=education&category=%EA%B5%90%EC%9C%A1&level=major&limit=500') {
        return {
          available: true,
          total: 1,
          items: [
            {
              provider: 'MUNI_TEST',
              content_type: 'education',
              active_count: 12,
              field_completeness: 83.3,
              complete_count: 6,
              target_count: 12,
              fee_count: 6,
              date_count: 12,
              place_count: 12,
              category_count: 12,
              time_count: 6,
              encoding_issue_count: 0,
              bad_count: 0,
              warning_count: 0,
              unchecked_count: 12,
              provider_urls: [],
            },
          ],
        };
      }
      if (path === '/quality/providers?provider=MUNI_LOCATION&level=major&limit=500') {
        return {
          available: true,
          total: 1,
          items: [
            {
              provider: 'MUNI_LOCATION',
              content_type: 'education',
              active_count: 8,
              field_completeness: 62.5,
              complete_count: 2,
              target_count: 8,
              fee_count: 3,
              date_count: 8,
              place_count: 4,
              category_count: 8,
              time_count: 4,
              encoding_issue_count: 1,
              average_score: 48.2,
              bad_count: 3,
              warning_count: 2,
              unchecked_count: 1,
              provider_urls: [],
            },
          ],
        };
      }
      if (path === '/quality/gap-samples?provider=MUNI_TEST&content_type=education&category=%EA%B5%90%EC%9C%A1&level=major&limit=10') {
        return {
          available: true,
          provider: 'MUNI_TEST',
          total: 4,
          missing_counts: { target: 0, fee: 4, date: 1, place: 0, category: 0, time: 2 },
          suggested_parser_family: 'municipal board/list + detail',
          suggestion_reason: '지자체 목록에서 상세 페이지를 따라가는 공통 family가 적합합니다.',
          items: [
            {
              id: 'course-gap-1',
              title: '누락 샘플 강좌',
              branch: '테스트 기관',
              status: 'OPEN',
              missing_fields: ['fee', 'time'],
              current_parser: 'generic_table',
              source_url: 'https://example.go.kr/lecture/list.do',
            },
          ],
        };
      }
      if (
        path === '/quality/address-fixes?limit=100'
        || path === '/quality/address-fixes?limit=100&provider=MUNI_LOCATION'
      ) {
        return {
          available: true,
          total: 1,
          limit: 100,
          offset: 0,
          geocode_fields_available: [
            'geocode_status',
            'geocode_reason_code',
            'geocode_attempt_count',
            'geocode_candidates',
            'geocode_next_retry_at',
            'geocode_last_error',
            'geocode_last_attempt_at',
          ],
          items: [
            {
              id: 'branch-id',
              provider: 'MUNI_LOCATION',
              branch_code: 'branch-1',
              name: '위치 미완성 지점',
              address: '서울특별시 중구',
              lat: null,
              lon: null,
              geocode_status: 'retrying',
              geocode_reason_code: 'ambiguous',
              geocode_attempt_count: 2,
              geocode_candidates: [{ address: '후보 1' }, { address: '후보 2' }],
              geocode_next_retry_at: '2026-08-06T10:00:00Z',
              geocode_last_error: 'temporary provider error',
              geocode_last_attempt_at: '2026-08-06T09:00:00Z',
            },
          ],
        };
      }
      if (path.startsWith('/quality/issues')) {
        return { available: true, total: 0, limit: 100, offset: 0, items: [] };
      }
      throw new Error(`Unexpected API path: ${path}`);
    });
  });

  it('shows major-category field quality and explicit encoding damage counts', async () => {
    renderPage();

    expect(await screen.findByText('교육')).toBeInTheDocument();
    expect(screen.queryByText('미술·공예')).not.toBeInTheDocument();
    expect(screen.queryByText('??깃문??덈뮸')).not.toBeInTheDocument();
    expect(screen.getByText('원본 손상 4건')).toBeInTheDocument();
    expect(screen.getByText('필드 평균 충족')).toBeInTheDocument();
    expect(screen.getAllByText('83.3%').length).toBeGreaterThan(0);
    expect(screen.getByText('원본 인코딩 손상')).toBeInTheDocument();
    expect(screen.getByText('대카테고리별 품질')).toBeInTheDocument();
    expect(screen.getByText('위치 미완성').nextElementSibling).toHaveTextContent('11');
    expect(await screen.findByText('위치 보정 상태')).toBeInTheDocument();
    expect(screen.getByText('geocode_status')).toBeInTheDocument();
    expect(screen.getByText('geocode_reason_code')).toBeInTheDocument();
    expect(screen.getByText('geocode_attempt_count')).toBeInTheDocument();
    expect(screen.getByText('geocode_candidates')).toBeInTheDocument();
    expect(screen.getByText('geocode_next_retry_at')).toBeInTheDocument();
    expect(screen.getByText('geocode_last_error')).toBeInTheDocument();
    expect(screen.getByText('geocode_last_attempt_at')).toBeInTheDocument();
    expect(screen.getByText('retrying')).toBeInTheDocument();
    expect(screen.getByText('2개 후보')).toBeInTheDocument();
    expect(screen.getByText('temporary provider error')).toBeInTheDocument();

    fireEvent.click(screen.getByText('교육'));
    expect(await screen.findByText('교육 Provider별 품질')).toBeInTheDocument();
    expect(await screen.findByText('MUNI_TEST')).toBeInTheDocument();
    expect(screen.getByText('교육').closest('tr')).toHaveClass('selected-row');
    expect(mockedOpsApi).toHaveBeenCalledWith(
      '/quality/providers?content_type=education&category=%EA%B5%90%EC%9C%A1&level=major&limit=500',
    );

    fireEvent.click(await screen.findByText('MUNI_TEST'));
    expect(await screen.findByText('municipal board/list + detail')).toBeInTheDocument();
    expect(screen.getByText('누락 샘플 강좌')).toBeInTheDocument();
    expect(screen.getByText('fee, time')).toBeInTheDocument();
    expect(mockedOpsApi).toHaveBeenCalledWith(
      '/quality/gap-samples?provider=MUNI_TEST&content_type=education&category=%EA%B5%90%EC%9C%A1&level=major&limit=10',
    );
    expect(mockedOpsApi).toHaveBeenCalledWith('/quality/address-fixes?limit=100');
    expect(mockedOpsApi).toHaveBeenCalledWith('/quality/issues?limit=100');
  });

  it('uses an exact Provider deep link for provider-scoped quality evidence', async () => {
    renderPage('/data-quality?provider=MUNI_LOCATION');

    expect(await screen.findByText('Provider 집중 보기')).toBeInTheDocument();
    expect(screen.getAllByText('MUNI_LOCATION').length).toBeGreaterThan(0);
    expect(await screen.findByRole('heading', { name: 'MUNI_LOCATION Provider별 품질' })).toBeInTheDocument();
    expect((await screen.findAllByText('62.5%')).length).toBeGreaterThan(0);
    expect(screen.getByRole('heading', { name: 'MUNI_LOCATION 품질 문제' })).toBeInTheDocument();
    expect(mockedOpsApi).toHaveBeenCalledWith(
      '/quality/providers?provider=MUNI_LOCATION&level=major&limit=500',
    );
    expect(mockedOpsApi).toHaveBeenCalledWith(
      '/quality/address-fixes?limit=100&provider=MUNI_LOCATION',
    );
    expect(mockedOpsApi).toHaveBeenCalledWith(
      '/quality/issues?limit=100&provider=MUNI_LOCATION',
    );
    expect(mockedOpsApi).not.toHaveBeenCalledWith('/quality/address-fixes?limit=100');
    expect(mockedOpsApi).not.toHaveBeenCalledWith('/quality/issues?limit=100');
    expect(screen.getByRole('link', { name: '개선 큐' })).toHaveAttribute('href', '/crawler-improvements');
  });
});
