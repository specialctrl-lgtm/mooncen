import '@testing-library/jest-dom/vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { opsApi } from '../api';
import { OpsProvider } from '../context';
import CrawlerImprovementsPage from './CrawlerImprovementsPage';

vi.mock('../api', () => ({ opsApi: vi.fn() }));
const mockedOpsApi = vi.mocked(opsApi);

afterEach(cleanup);

function item(overrides: Record<string, unknown> = {}) {
  return {
    provider: 'HOMEPLUS',
    priority: 'P1',
    score: 70,
    evidence_complete: true,
    active_course_count: 1200,
    stale_48h_count: 20,
    stale_7d_count: 3,
    freshness_unknown_count: 0,
    consecutive_failures: 2,
    last_run_status: 'failed',
    last_run_at: '2026-08-15T01:00:00Z',
    last_success_at: '2026-08-14T01:00:00Z',
    quality_average_score: 81.5,
    quality_bad_count: 12,
    active_quality_issue_count: 4,
    error_category: 'source_contract',
    error_code: 'required_field_missing',
    reasons: [{ code: 'consecutive_failures', label: '연속 실패 2회', points: 30 }],
    recommended_action: { code: 'inspect_runs', label: '실행 근거 확인', href: '/crawlers' },
    ...overrides,
  };
}

function payload(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: 1,
    available: true,
    complete: true,
    generated_at: '2026-08-15T02:00:00Z',
    total: 1,
    limit: 500,
    truncated: false,
    sources: { runs: true, freshness: true, quality_scores: true, quality_issues: true },
    items: [item()],
    ...overrides,
  };
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <OpsProvider session={{ user: { id: 'viewer', email: 'viewer@example.test', name: '조회자' }, role: 'viewer', environment: 'production' }}>
        <MemoryRouter><CrawlerImprovementsPage /></MemoryRouter>
      </OpsProvider>
    </QueryClientProvider>,
  );
}

describe('CrawlerImprovementsPage', () => {
  beforeEach(() => mockedOpsApi.mockReset());

  it('ranks priority before score and exposes each point contribution', async () => {
    mockedOpsApi.mockResolvedValue(payload({
      items: [
        item({ provider: 'P2_HIGH_SCORE', priority: 'P2', score: 99 }),
        item({ provider: 'P0_LOWER_SCORE', priority: 'P0', score: 80, reasons: [{ code: 'stale_7d', label: '7일 초과 강좌', points: 45 }] }),
        item({ provider: 'P0_HIGHER_SCORE', priority: 'P0', score: 90, reasons: [{ code: 'failed', label: '최근 실행 실패', points: 55 }] }),
      ],
    }));
    renderPage();

    const providers = await screen.findAllByTestId('improvement-provider');
    expect(providers.map((provider) => provider.textContent)).toEqual([
      'P0_HIGHER_SCORE',
      'P0_LOWER_SCORE',
      'P2_HIGH_SCORE',
    ]);
    expect(screen.getByText('최근 실행 실패')).toBeInTheDocument();
    expect(screen.getByText('+55점')).toBeInTheDocument();
    expect(mockedOpsApi).toHaveBeenCalledWith('/crawlers/improvement-queue?limit=500');

    fireEvent.click(screen.getByRole('button', { name: 'P2' }));
    expect(screen.getByRole('button', { name: 'P2' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: '전체' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getAllByTestId('improvement-provider').map((provider) => provider.textContent)).toEqual(['P2_HIGH_SCORE']);
    fireEvent.click(screen.getByRole('button', { name: '전체' }));
    fireEvent.change(screen.getByLabelText('Provider 검색'), { target: { value: 'lower' } });
    expect(screen.getAllByTestId('improvement-provider').map((provider) => provider.textContent)).toEqual(['P0_LOWER_SCORE']);
  });

  it('keeps unavailable and null evidence unknown instead of turning it into zero', async () => {
    mockedOpsApi.mockResolvedValue(payload({
      complete: false,
      sources: { runs: true, freshness: false, quality_scores: false, quality_issues: true },
      items: [item({
        provider: 'UNKNOWN_EVIDENCE',
        evidence_complete: false,
        score: null,
        active_course_count: null,
        stale_48h_count: null,
        stale_7d_count: null,
        freshness_unknown_count: null,
        consecutive_failures: null,
        last_run_status: null,
        last_run_at: null,
        last_success_at: null,
        quality_average_score: null,
        quality_bad_count: null,
        active_quality_issue_count: null,
        error_category: null,
        error_code: null,
        reasons: [{ code: 'future_reason_code', label: '', points: null }],
      })],
    }));
    renderPage();

    expect(await screen.findByRole('alert')).toHaveTextContent('일부 근거만 반영된 개선 큐입니다.');
    expect(screen.getByRole('alert')).toHaveTextContent('최신성, 품질 점수');
    expect(screen.getByRole('alert')).toHaveTextContent('0으로 계산하거나 표시하지 않습니다');
    expect(screen.getAllByText('확인 불가').length).toBeGreaterThan(4);
    expect(screen.getByText('알 수 없는 근거 (future_reason_code)')).toBeInTheDocument();
    expect(screen.queryByText(/^0$/)).not.toBeInTheDocument();
  });

  it('adds exact provider links and refuses an external recommended action', async () => {
    mockedOpsApi.mockResolvedValue(payload({
      items: [item({
        provider: 'MUNI_TEST',
        recommended_action: { code: 'unsafe', label: '근거 열기', href: 'https://evil.example/phish' },
      })],
    }));
    renderPage();

    expect(await screen.findByRole('link', { name: 'MUNI_TEST: 근거 열기' })).toHaveAttribute('href', '/crawlers?provider=MUNI_TEST');
    expect(screen.getByRole('link', { name: 'MUNI_TEST: 실행·오류 근거' })).toHaveAttribute('href', '/crawlers?provider=MUNI_TEST');
    expect(screen.getByRole('link', { name: 'MUNI_TEST: 품질 확인' })).toHaveAttribute('href', '/data-quality?provider=MUNI_TEST');
    expect(screen.getByRole('link', { name: 'MUNI_TEST: 수정 초안·검증' })).toHaveAttribute('href', '/crawler-studio?provider=MUNI_TEST');
    expect(document.querySelector('a[href^="https://evil.example"]')).not.toBeInTheDocument();
  });

  it('preserves an allowed internal action and overrides its provider with the ranked item', async () => {
    mockedOpsApi.mockResolvedValue(payload({
      items: [item({
        provider: 'SAFE_PROVIDER',
        recommended_action: { code: 'quality', label: '품질 근거 확인', href: '/data-quality?provider=WRONG&scope=active' },
      })],
    }));
    renderPage();

    expect(await screen.findByRole('link', { name: 'SAFE_PROVIDER: 품질 근거 확인' })).toHaveAttribute(
      'href',
      '/data-quality?provider=SAFE_PROVIDER&scope=active',
    );
  });

  it('shows available=false as unavailable evidence and never renders an empty zero queue', async () => {
    mockedOpsApi.mockResolvedValue(payload({ available: false, complete: false, sources: null, items: [] }));
    renderPage();

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('크롤러 개선 우선순위를 계산할 근거에 연결되지 않았습니다.');
    expect(alert).toHaveTextContent('표시할 수 없는 수치는 0이 아닙니다');
    expect(screen.queryByLabelText('우선순위별 크롤러 개선 후보')).not.toBeInTheDocument();
    expect(screen.queryByText(/0개 Provider/)).not.toBeInTheDocument();
  });

  it('reports a truncated server result instead of making client search look complete', async () => {
    mockedOpsApi.mockResolvedValue(payload({ total: 700, limit: 500, truncated: true }));
    renderPage();

    const status = await screen.findByRole('status');
    expect(status).toHaveTextContent('개선 후보 일부만 표시합니다.');
    expect(status).toHaveTextContent('전체 700개 중 상위 1개 Provider');
    expect(status).toHaveTextContent('API 페이지네이션이 필요합니다');
  });
});
