import '@testing-library/jest-dom/vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { opsApi } from '../api';
import { OpsProvider } from '../context';
import CrawlerAnalyticsPage from './CrawlerAnalyticsPage';

vi.mock('../api', () => ({ opsApi: vi.fn() }));
const mockedOpsApi = vi.mocked(opsApi);
afterEach(cleanup);
const component = (payload: Record<string, unknown>) => ({ available: true, has_data: true, reasons: [], ...payload });
const batchId = '11111111-1111-4111-8111-111111111111';

function completePayload() {
  return {
    schema_version: 2, available: true, complete: false, partial: true, environment: 'staging', generated_at: '2026-08-12T03:00:00Z', window_hours: 24, heartbeat_timeout_seconds: 120, reasons: [{ section: 'correlations', component: 'generations', code: 'generation_attribution_evidence_unavailable', message: '세대 fence 증거가 없습니다.' }],
    deployment: { available: true, complete: true, has_data: true, reasons: [], components: { rollout: component({ latest: { rollout_epoch: 7, status: 'running', code_version: 'v7', created_at: '2026-08-12T01:00:00Z' } }), versions: component({ summary: { desired_workers: 2, ready_current_workers: 1, outdated_workers: 1, failed_workers: 0 }, items: [] }) } },
    collection: { available: true, complete: true, has_data: true, reasons: [], components: { runs: component({ totals: { run_count: 8, successful_runs: 7, failed_runs: 1, collected_count: 1234, new_count: 23, updated_count: 91 } }), batches: component({ outcomes: { batch_count: 2 } }), validation: component({ totals: { sealed_batch_count: 2, valid_courses: 1200, invalid_courses: 34, held_for_approval_batches: 1 } }) } },
    providers: { available: true, complete: true, has_data: true, reasons: [], components: { collection: component({ items: [{ provider: 'HOMEPLUS', run_count: 3, successful_runs: 2, failed_runs: 1, collected_count: 900, new_count: 20, updated_count: 50, failed_item_count: 4, success_rate: 66.7, last_run_at: '2026-08-12T02:00:00Z' }], total: 1 }) } },
    quality: { available: true, complete: true, has_data: true, reasons: [], components: { scores: component({ summary: { average_score: 91.2, good_courses: 1100, bad_courses: 30, incomplete_courses: 12 }, providers: [] }), issues: component({ summary: { active_issues: 4, active_critical_issues: 1, blocked_sync_issues: 2 } }) } },
    workers: { available: true, complete: true, has_data: true, reasons: [], components: { health: component({ summary: { worker_count: 2, healthy_workers: 1, stale_workers: 1, maintenance_workers: 0 }, items: [{ id: 'worker-id', name: 'crawler-a', hostname: 'crawler-a', status: 'healthy', maintenance_mode: false, heartbeat_stale: false, health_state: 'healthy', last_seen_at: '2026-08-12T02:59:00Z' }] }) } },
    queue: { available: true, complete: true, has_data: true, reasons: [], components: { health: component({ metrics: { ready_jobs: 3, delayed_jobs: 1, running_jobs: 2, expired_leases: 0, dead_lettered_jobs: 0, oldest_ready_age_seconds: 45 } }) } },
    correlations: { available: true, complete: false, has_data: true, reasons: [{ component: 'generations', code: 'generation_attribution_evidence_unavailable', message: '세대 fence 증거가 없습니다.' }], components: {
      attribution: { available: true, has_data: true, reasons: [], summary: { total_attempts: 5, attributed_attempts: 3, legacy_unattributed_attempts: 1, rejected_mismatched_attempts: 1 } },
      generations: component({ total: 1, truncated: false, items: [{ rollout_id: '22222222-2222-4222-8222-222222222222', generation: 7, code_versions: ['crawler-v7'], attempt_count: 3, failed_attempts: 0, retried_tasks: 1, lease_lost_attempts: 0, collected_count: 120, new_count: 20, updated_count: 10, invalid_courses: 1 }] }),
      batches: component({ total: 1, truncated: false, items: [{ id: batchId, status: 'success', scheduled_slot: '2026-08-12T02:00:00Z', providers: ['MUNI_TEST'], rollout_id: '22222222-2222-4222-8222-222222222222', generation: 7, attribution_state: 'attributed', collected_count: 120, new_count: 20, updated_count: 10, failed_item_count: 1, retry_attempts: 1, lease_lost_attempts: 1, duration_seconds: 42, invalid_courses: 1 }] }),
      quality: { available: false, has_data: null, reasons: [{ code: 'generation_quality_attribution_unavailable', message: '품질 행에 불변 배치·시도 연결이 없습니다.' }], total: null, truncated: null, items: null },
    } },
  };
}

function batchDetailPayload() {
  return {
    available: true,
    environment: 'staging',
    batch_id: batchId,
    item: { id: batchId, status: 'success', artifact_digest: 'a'.repeat(64), code_version: 'crawler-v7', total_courses: 120, valid_courses: 119, invalid_courses: 1, validation: { promotion_eligible: false } },
    tasks: [{ task_key: 'MUNI_TEST:0', provider: 'MUNI_TEST', job_status: 'success', attempt_no: 2, attempt_status: 'success', generation: null, attribution_state: 'identity_match_unattributed', total_count: 120, new_count: 20, updated_count: 10, failed_count: 1, retry_count: 1, lease_lost_attempts: 1, attempt_duration_seconds: 42, error_code: null }],
    total_tasks: 1,
    truncated: false,
    attribution: { available: false, has_data: true, reasons: [{ code: 'generation_attribution_evidence_unavailable', message: '세대 fence 증거가 없습니다.' }], semantics: 'artifact_code_config_agent_identity_match_only' },
    reasons: [],
  };
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <OpsProvider session={{ user: { id: 'viewer', email: 'viewer@example.test', name: '조회자' }, role: 'viewer', environment: 'staging' }}>
        <MemoryRouter><CrawlerAnalyticsPage /></MemoryRouter>
      </OpsProvider>
    </QueryClientProvider>,
  );
}

describe('CrawlerAnalyticsPage', () => {
  beforeEach(() => mockedOpsApi.mockReset());

  it('renders central correlation metrics, changes the window, and drills into the same control API', async () => {
    mockedOpsApi.mockImplementation(async (path?: string) => {
      if (!path) return undefined as never;
      if (path.startsWith(`/crawlers/analytics/batches/${batchId}?`)) return batchDetailPayload();
      if (path.startsWith('/crawlers/analytics?')) return completePayload();
      throw new Error(`Unexpected API path: ${path}`);
    });
    renderPage();

    expect(await screen.findByText('HOMEPLUS')).toBeInTheDocument();
    expect(screen.getByText('1,234')).toBeInTheDocument();
    expect(screen.getAllByText('crawler-a')).toHaveLength(2);
    expect(screen.getByText(/활성 이슈 4건/)).toBeInTheDocument();
    expect(screen.getByText('릴리스 세대별 수집 상관관계')).toBeInTheDocument();
    expect(screen.getByText('중앙 수집 배치')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '이슈 상세' })).not.toBeInTheDocument();

    expect(screen.getByText('세대 정확 귀속')).toBeInTheDocument();
    expect(screen.getByText('레거시 미귀속')).toBeInTheDocument();
    expect(screen.getByText('증거 불일치 거부')).toBeInTheDocument();
    expect(screen.getByText(/불변 배치·시도 연결이 없어/)).toBeInTheDocument();

    fireEvent.click(screen.getByText('attributed'));
    expect(await screen.findByRole('dialog', { name: '중앙 수집 배치 상세' })).toBeInTheDocument();
    expect(await screen.findByText('MUNI_TEST:0')).toBeInTheDocument();
    expect(mockedOpsApi).toHaveBeenCalledWith(`/crawlers/analytics/batches/${batchId}?environment=staging&task_limit=100&task_offset=0`);

    fireEvent.click(screen.getByRole('button', { name: '상세 닫기' }));
    fireEvent.change(screen.getByLabelText('분석 기간'), { target: { value: '72' } });
    await waitFor(() => expect(mockedOpsApi).toHaveBeenCalledWith('/crawlers/analytics?environment=staging&window_hours=72&correlation_limit=25'));
  });

  it('shows unavailable as unknown rather than rendering zero metrics', async () => {
    const rawReason = { code: 'crawler_control_database_not_configured', message: 'The dedicated crawler-control read-only API pool is not configured' };
    const unavailableSection = { available: false, complete: false, has_data: null, reasons: [rawReason], components: {} };
    mockedOpsApi.mockResolvedValue({ schema_version: 2, available: false, complete: false, partial: false, environment: 'staging', generated_at: '2026-08-12T03:00:00Z', window_hours: 24, heartbeat_timeout_seconds: 120, reasons: [rawReason], deployment: unavailableSection, collection: unavailableSection, providers: unavailableSection, quality: unavailableSection, workers: unavailableSection, queue: unavailableSection, correlations: unavailableSection });
    renderPage();

    expect(await screen.findByText('중앙 크롤러 분석 데이터에 연결되지 않았습니다.')).toBeInTheDocument();
    expect(screen.getByText(/crawler-control 전용 읽기 연결이 아직 설정되지 않았습니다/)).toBeInTheDocument();
    expect(screen.queryByText(/The dedicated crawler-control read-only API pool/)).not.toBeInTheDocument();
    expect(screen.getByText(/모든 지표가 0이라는 뜻이 아닙니다/)).toBeInTheDocument();
    expect(screen.queryByText('작업 대기열')).not.toBeInTheDocument();
  });
});
