import '@testing-library/jest-dom/vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { opsApi } from '../api';
import DashboardPage from './DashboardPage';

vi.mock('../api', () => ({
  opsApi: vi.fn(),
}));

const mockedOpsApi = vi.mocked(opsApi);

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('DashboardPage service placement', () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    mockedOpsApi.mockImplementation(async (path: string) => {
      if (path === '/dashboard/summary') {
        return {
          generated_at: '2026-08-11T00:00:00Z',
          environment: 'production',
          overall_status: 'warning',
          components: [
            {
              type: 'crawler',
              name: 'Crawler',
              status: 'warning',
              service_host: 'mooncen',
              reporter_hostname: 'mooncen',
              observed_runtime_host: null,
              configured_owner_host: 'gen1crawler',
              topology_host: 'gen1crawler',
              last_checked_at: '2026-08-11T00:00:00Z',
            },
            {
              type: 'backend',
              name: 'Backend',
              status: 'healthy',
              service_host: 'cloud',
              observed_runtime_host: 'cloud',
              configured_owner_host: 'cloud',
              topology_host: 'cloud',
              last_checked_at: '2026-08-11T00:00:00Z',
            },
          ],
          agents: { connected: 1, total: 1, status: 'healthy' },
          latest_deployment: null,
        };
      }
      if (path === '/dashboard/collection-summary') {
        return { available: false };
      }
      if (path === '/dashboard/quality-summary') {
        return { available: false, counts: {} };
      }
      if (path === '/dashboard/alerts?limit=10') {
        return { available: true, items: [] };
      }
      if (path === '/dashboard/recent-jobs?limit=8') {
        return { available: true, items: [] };
      }
      if (path === '/dashboard/visitor-summary?days=7') {
        return {
          available: true,
          summary: {
            today: { start_date: '2026-08-14', end_date: '2026-08-14', visits: 12, requests: 120, partial: true, estimated: true },
            yesterday: { start_date: '2026-08-13', end_date: '2026-08-13', visits: 10, requests: 100, partial: false, estimated: true },
            last_7_days: { start_date: '2026-08-08', end_date: '2026-08-14', visits: 70, requests: 700, partial: true, estimated: true },
            previous_7_days: null,
          },
          series: Array.from({ length: 7 }, (_, index) => ({
            date: `2026-08-${String(index + 8).padStart(2, '0')}`,
            visits: index + 6,
            requests: (index + 6) * 10,
            partial: index === 6,
            estimated: true,
          })),
          schema_version: 1,
          timezone: 'Asia/Seoul',
          requested_days: 7,
          estimated: true,
          source: {
            provider: 'cloudflare',
            dataset: 'httpRequestsAdaptiveGroups',
            hostname: 'mooncen.kr',
            hostnames: ['mooncen.kr', 'www.mooncen.kr'],
            request_source: 'eyeball',
            granularity: 'hour',
            adaptive_sampling: true,
            values_are_estimates: true,
          },
          sampling: {
            method: 'cloudflare_adaptive',
            confidence_level: 0.95,
            confidence_intervals_requested: true,
            validated_points: 7,
            max_sample_interval: 1,
            min_sample_size: 100,
            aggregate_bounds_available: false,
          },
          metric_definitions: {
            visits: { label: 'Visits', unique_visitors: false },
            requests: { label: 'HTTP requests', pageviews: false },
            pageviews: { available: false },
          },
          data_through: '2026-08-14T00:00:00Z',
          generated_at: '2026-08-14T00:05:00Z',
        };
      }
      throw new Error(`Unexpected API path: ${path}`);
    });
  });

  it('shows configured owner and reporter without promoting the reporter to runtime', async () => {
    renderPage();

    expect(await screen.findByText(/설정 gen1crawler.*보고 mooncen/i)).toBeInTheDocument();
    expect(screen.getByText(/설정 cloud/i)).toBeInTheDocument();
    expect(screen.queryByText(/실행 mooncen/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/보고 cloud/i)).not.toBeInTheDocument();
  });

  it('shows visitor summaries and an accessible 7-day trend without calling visits unique users', async () => {
    renderPage();

    expect(await screen.findByRole('heading', { name: 'MoonCen 방문 현황' })).toBeInTheDocument();
    expect(mockedOpsApi).toHaveBeenCalledWith('/dashboard/visitor-summary?days=7');
    expect(await screen.findByText('오늘 방문 추정치')).toBeInTheDocument();
    expect(screen.getByText('어제 방문 추정치')).toBeInTheDocument();
    expect(screen.getByText('최근 7일 방문 추정치')).toBeInTheDocument();
    expect(screen.queryByText('직전 7일 방문 추정치')).not.toBeInTheDocument();
    expect(screen.getByText('70회')).toBeInTheDocument();
    expect(screen.getByText(/HTTP 요청 700회/)).toBeInTheDocument();
    expect(screen.getByText(/방문 추정치는 고유 사용자 수가 아니며/)).toBeInTheDocument();
    expect(screen.getByText(/자동화된 트래픽이 포함될 수 있습니다/)).toBeInTheDocument();
    expect(screen.getByText(/Adaptive Sampling 기반 추정치\(95% 신뢰수준\)/)).toBeInTheDocument();
    expect(screen.getByText(/기간 합계 오차 범위는 제공되지 않습니다/)).toBeInTheDocument();
    expect(screen.getByText(/페이지뷰가 아닙니다/)).toBeInTheDocument();
    expect(screen.getByText(/한국시간\(KST\) 기준.*완료된 시간 단위/)).toBeInTheDocument();
    expect(screen.getByText('최근 7일 일별 방문 추정치')).toBeInTheDocument();
    expect(screen.getByLabelText('8.14 방문 추정 12회, HTTP 요청 120회, 집계 중')).toBeInTheDocument();
  });
});

describe('DashboardPage visitor analytics unavailable state', () => {
  let visitorReasonCode = 'CLOUDFLARE_ANALYTICS_NOT_CONFIGURED';

  afterEach(() => cleanup());

  beforeEach(() => {
    visitorReasonCode = 'CLOUDFLARE_ANALYTICS_NOT_CONFIGURED';
    mockedOpsApi.mockImplementation(async (path: string) => {
      if (path === '/dashboard/summary') {
        return {
          generated_at: '2026-08-14T00:00:00Z',
          environment: 'production',
          overall_status: 'healthy',
          components: [],
          agents: { connected: 1, total: 1, status: 'healthy' },
          latest_deployment: null,
        };
      }
      if (path === '/dashboard/collection-summary') return { available: false };
      if (path === '/dashboard/quality-summary') return { available: false, counts: {} };
      if (path === '/dashboard/alerts?limit=10') return { available: true, items: [] };
      if (path === '/dashboard/recent-jobs?limit=8') return { available: true, items: [] };
      if (path === '/dashboard/visitor-summary?days=7') {
        return {
          schema_version: 1,
          available: false,
          reason_code: visitorReasonCode,
          timezone: 'Asia/Seoul',
          requested_days: 7,
          estimated: true,
          source: {
            provider: 'cloudflare',
            dataset: 'httpRequestsAdaptiveGroups',
            hostname: 'mooncen.kr',
            hostnames: ['mooncen.kr', 'www.mooncen.kr'],
            request_source: 'eyeball',
            granularity: 'hour',
            adaptive_sampling: true,
            values_are_estimates: true,
          },
          sampling: {
            method: 'cloudflare_adaptive',
            confidence_level: 0.95,
            confidence_intervals_requested: true,
            validated_points: 0,
            max_sample_interval: null,
            min_sample_size: null,
            aggregate_bounds_available: false,
          },
          metric_definitions: {},
          summary: null,
          series: [],
          data_through: null,
          generated_at: '2026-08-14T00:00:00Z',
        };
      }
      throw new Error(`Unexpected API path: ${path}`);
    });
  });

  it('shows a Korean reason and does not render misleading zero metrics', async () => {
    renderPage();

    expect(await screen.findByText('Cloudflare 방문 분석 연동이 아직 설정되지 않았습니다.')).toBeInTheDocument();
    expect(screen.queryByText('오늘 방문 추정치')).not.toBeInTheDocument();
    expect(screen.queryByText('최근 7일 일별 방문 추정치')).not.toBeInTheDocument();
    expect(screen.queryByText('0회')).not.toBeInTheDocument();
  });

  it('uses a safe Korean fallback for an unknown reason without rendering zero metrics', async () => {
    visitorReasonCode = 'CLOUDFLARE_ANALYTICS_FUTURE_REASON';
    renderPage();

    expect(await screen.findByText('방문 분석 데이터를 사용할 수 없습니다. Cloudflare 연동 상태를 확인해 주세요.')).toBeInTheDocument();
    expect(screen.queryByText('오늘 방문 추정치')).not.toBeInTheDocument();
    expect(screen.queryByText('0회')).not.toBeInTheDocument();
    expect(screen.queryByText(visitorReasonCode)).not.toBeInTheDocument();
  });

  it('localizes an unavailable Cloudflare query range without rendering estimates', async () => {
    visitorReasonCode = 'CLOUDFLARE_ANALYTICS_RANGE_UNAVAILABLE';
    renderPage();

    expect(await screen.findByText('요청한 기간의 Cloudflare 방문 분석 범위를 조회할 수 없습니다. 잠시 후 다시 확인해 주세요.')).toBeInTheDocument();
    expect(screen.queryByText('오늘 방문 추정치')).not.toBeInTheDocument();
    expect(screen.queryByText('0회')).not.toBeInTheDocument();
  });
});
