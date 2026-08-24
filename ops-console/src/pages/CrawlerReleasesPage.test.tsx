import '@testing-library/jest-dom/vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { opsApi } from '../api';
import { OpsProvider } from '../context';
import CrawlerReleasesPage, { crawlerReleaseWorkerSetDigest } from './CrawlerReleasesPage';

vi.mock('../api', () => ({ opsApi: vi.fn() }));
const mockedOpsApi = vi.mocked(opsApi);
afterEach(cleanup);
const rolloutId = '11111111-1111-4111-8111-111111111111';
const actionId = '22222222-2222-4222-8222-222222222222';
const targetDigest = 'a'.repeat(64);
const baselineDigest = 'b'.repeat(64);
const actionCapabilities = {
  build: { available: false, reason: 'immutable_builder_evidence_handoff_not_implemented' },
  register_artifact: { available: false, reason: 'immutable_builder_evidence_handoff_not_implemented' },
  create_canary: { available: true, reason: null },
  advance_rollout: { available: true, reason: null },
  pause_rollout: { available: true, reason: null },
  rollback_rollout: { available: true, reason: null },
  complete_rollback: { available: true, reason: null },
};

function renderPage(role: 'viewer' | 'admin' = 'admin', entry = '/crawler-releases') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <OpsProvider session={{ user: { id: 'admin', email: 'admin@example.test', name: '관리자' }, role, environment: 'staging' }}>
        <MemoryRouter initialEntries={[entry]}>
          <Routes>
            <Route path="/crawler-releases" element={<CrawlerReleasesPage />} />
            <Route path="/crawler-releases/rollouts/:rolloutId" element={<CrawlerReleasesPage />} />
            <Route path="/crawler-releases/actions/:actionId" element={<CrawlerReleasesPage />} />
          </Routes>
        </MemoryRouter>
      </OpsProvider>
    </QueryClientProvider>,
  );
}

function availableApi(path: string, init?: RequestInit): unknown {
  const rollout = { id: rolloutId, rollout_epoch: 4, artifact_digest: baselineDigest, status: 'success', strategy: 'complete', requested_worker_count: 2, created_at: '2026-08-12T01:00:00Z' };
  if (path === '/crawler-control/summary') return {
    available: true,
    environment: 'staging',
    artifact_count: 1,
    worker_count: 2,
    unhealthy_worker_count: 1,
    pending_action_count: 0,
    active_rollout: rollout,
    action_capabilities: actionCapabilities,
  };
  if (path === '/crawler-control/artifacts?limit=100&offset=0') return { available: true, total: 1, limit: 100, offset: 0, items: [{ artifact_digest: targetDigest, code_version: 'v2026.08.12', config_revision: 'config-7', size_bytes: 4096, key_id: 'release-key-1', created_at: '2026-08-12T00:00:00Z' }] };
  if (path === '/crawler-control/rollouts?limit=100&offset=0') return { available: true, total: 1, limit: 100, offset: 0, items: [rollout] };
  if (path === '/crawler-control/workers') return { available: true, items: [{ worker_key: 'worker-a', hostname: 'crawler-a', rollout_id: rolloutId, reported_rollout_id: rolloutId, generation: 4, desired_status: 'active', cohort: 'canary', artifact_digest: baselineDigest, code_version: 'v2026.08.12', config_revision: 'config-7', report_status: 'ready', reported_generation: 4, reported_artifact_digest: baselineDigest, reported_code_version: 'v2026.08.12', reported_config_revision: 'config-7', release_converged: true, reported_at: '2026-08-12T02:00:00Z' }] };
  if (path === '/crawler-control/actions?limit=100&offset=0') return { available: true, total: 0, limit: 100, offset: 0, items: [] };
  if (path === '/crawler-control/actions' && init?.method === 'POST') return { available: true, replayed: false, item: { id: actionId, action: 'create_canary', status: 'queued', approval_status: 'pending', request_digest: 'c'.repeat(64), expected_generation: 1, requested_by: 'admin', reason: 'canary validation', created_at: '2026-08-12T03:00:00Z' } };
  if (path === `/crawler-control/actions/${actionId}`) return { available: true, item: { id: actionId, action: 'create_canary', status: 'queued', approval_status: 'pending', request_digest: 'c'.repeat(64), expected_generation: 1, requested_by: 'admin', reason: 'canary validation', created_at: '2026-08-12T03:00:00Z' } };
  throw new Error(`Unexpected API path: ${path}`);
}

describe('CrawlerReleasesPage', () => {
  beforeEach(() => mockedOpsApi.mockReset());

  it('uses the exact sorted worker-set SHA-256 identity', async () => {
    await expect(crawlerReleaseWorkerSetDigest(['worker-b', 'worker-a'])).resolves.toBe('7e575d5a3da9');
  });

  it('does not synthesize zero release metrics when the central database is unavailable', async () => {
    mockedOpsApi.mockImplementation(async (path: string) => {
      if (!path) return undefined as never;
      if (path === '/crawler-control/summary') return { available: false };
      if (path.includes('/artifacts') || path.includes('/rollouts') || path.includes('/actions')) return { available: false, total: 0, limit: 100, offset: 0, items: [] };
      if (path === '/crawler-control/workers') return { available: false, items: [] };
      throw new Error(`Unexpected API path: ${path}`);
    });
    renderPage('viewer');

    expect(await screen.findByText('중앙 크롤러 제어 DB에 연결되지 않았습니다.')).toBeInTheDocument();
    expect(screen.queryByText('등록 아티팩트')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '릴리스 요청' })).not.toBeInTheDocument();
  });

  it('queues a canary request bound to artifacts, generation, and the exact worker set', async () => {
    mockedOpsApi.mockImplementation(async (path: string, init?: RequestInit) => path ? availableApi(path, init) : undefined as never);
    renderPage();

    expect(await screen.findByText('v2026.08.12')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Canary 요청' }));
    fireEvent.change(screen.getByLabelText(/^전체 Rollout Worker keys/), { target: { value: 'worker-b, worker-a' } });
    fireEvent.change(screen.getByLabelText('변경 사유'), { target: { value: 'canary validation' } });
    const generatedRolloutId = (screen.getByLabelText(/신규 Rollout UUID/) as HTMLInputElement).value;
    const exactConfirmation = `CANARY ${generatedRolloutId} 5 ${targetDigest.slice(0, 12)} ${baselineDigest.slice(0, 12)} 7e575d5a3da9`;
    expect(await screen.findByText(exactConfirmation)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('확인 문구'), { target: { value: exactConfirmation } });
    fireEvent.click(screen.getByRole('button', { name: '감사 대기열에 요청' }));

    await waitFor(() => {
      const call = mockedOpsApi.mock.calls.find(([path, init]) => path === '/crawler-control/actions' && init?.method === 'POST');
      expect(call).toBeDefined();
      expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
        action: 'create_canary', environment: 'staging', rollout_id: generatedRolloutId, expected_generation: 5,
        artifact_digest: targetDigest, baseline_digest: baselineDigest, worker_keys: ['worker-b', 'worker-a'], confirmation: exactConfirmation,
      });
    });
  });

  it('shows that rollout requests require an independent approval receipt', async () => {
    mockedOpsApi.mockImplementation(async (path: string, init?: RequestInit) => path ? availableApi(path, init) : undefined as never);
    renderPage('admin', `/crawler-releases/actions/${actionId}`);

    expect(await screen.findByText('변경 요청에는 별도 운영자 승인이 필요합니다.')).toBeInTheDocument();
    expect(screen.getByText('pending')).toBeInTheDocument();
    expect(screen.getByText('c'.repeat(64))).toBeInTheDocument();
  });

  it('queues a distinct rollback completion only from a rolling-back rollout', async () => {
    const rollingBack = {
      id: rolloutId,
      rollout_epoch: 9,
      artifact_digest: targetDigest,
      previous_artifact_digest: baselineDigest,
      status: 'rolling_back',
      strategy: { state: 'rollback' },
      requested_worker_count: 2,
      created_at: '2026-08-12T01:00:00Z',
    };
    mockedOpsApi.mockImplementation(async (path: string, init?: RequestInit) => {
      if (!path) return undefined as never;
      if (path === '/crawler-control/summary') return { available: true, environment: 'staging', artifact_count: 1, worker_count: 2, unhealthy_worker_count: 0, pending_action_count: 0, active_rollout: rollingBack, action_capabilities: actionCapabilities };
      if (path === '/crawler-control/rollouts?limit=100&offset=0') return { available: true, total: 1, limit: 100, offset: 0, items: [rollingBack] };
      if (path === `/crawler-control/rollouts/${rolloutId}`) return { available: true, item: rollingBack, workers: [] };
      return availableApi(path, init);
    });
    renderPage('admin', `/crawler-releases/rollouts/${rolloutId}`);

    fireEvent.click(await screen.findByRole('button', { name: '롤백 완료 확인' }));
    fireEvent.change(screen.getByLabelText('변경 사유'), { target: { value: 'all workers reported rollback' } });
    const exactConfirmation = `COMPLETE_ROLLBACK ${rolloutId} 9`;
    expect(screen.getByText(exactConfirmation)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('확인 문구'), { target: { value: exactConfirmation } });
    fireEvent.click(screen.getByRole('button', { name: '감사 대기열에 요청' }));

    await waitFor(() => {
      const call = mockedOpsApi.mock.calls.find(([path, init]) => path === '/crawler-control/actions' && init?.method === 'POST');
      expect(call).toBeDefined();
      expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
        action: 'complete_rollback',
        environment: 'staging',
        rollout_id: rolloutId,
        expected_generation: 9,
        confirmation: exactConfirmation,
      });
    });
  });

  it('disables rollback completion while the independent approval gate is closed', async () => {
    const rollingBack = {
      id: rolloutId,
      rollout_epoch: 9,
      artifact_digest: targetDigest,
      previous_artifact_digest: baselineDigest,
      status: 'rolling_back',
      strategy: { state: 'rollback' },
      requested_worker_count: 2,
      created_at: '2026-08-12T01:00:00Z',
    };
    const lockedCapabilities = Object.fromEntries(
      Object.keys(actionCapabilities).map((action) => [action, {
        available: false,
        reason: 'independent_operator_approval_not_implemented',
      }]),
    );
    mockedOpsApi.mockImplementation(async (path: string, init?: RequestInit) => {
      if (!path) return undefined as never;
      if (path === '/crawler-control/summary') return {
        available: true,
        environment: 'staging',
        artifact_count: 1,
        worker_count: 2,
        unhealthy_worker_count: 0,
        pending_action_count: 0,
        active_rollout: rollingBack,
        action_capabilities: lockedCapabilities,
      };
      if (path === '/crawler-control/rollouts?limit=100&offset=0') return { available: true, total: 1, limit: 100, offset: 0, items: [rollingBack] };
      if (path === `/crawler-control/rollouts/${rolloutId}`) return { available: true, item: rollingBack, workers: [] };
      return availableApi(path, init);
    });
    renderPage('admin', `/crawler-releases/rollouts/${rolloutId}`);

    expect(await screen.findByRole('button', { name: '롤백 완료 확인' })).toBeDisabled();
  });

  it('does not render pre-contract rollout history as zero workers', async () => {
    mockedOpsApi.mockImplementation(async (path: string, init?: RequestInit) => {
      if (!path) return undefined as never;
      if (path === `/crawler-control/rollouts/${rolloutId}`) return {
        available: true,
        item: {
          id: rolloutId,
          rollout_epoch: 4,
          artifact_digest: baselineDigest,
          status: 'success',
          requested_worker_count: 2,
        },
        workers: [],
        worker_history_available: false,
        worker_history_reason: 'rollout_worker_history_predates_snapshot_contract',
      };
      return availableApi(path, init);
    });

    renderPage('viewer', `/crawler-releases/rollouts/${rolloutId}`);

    expect(await screen.findByText('이 Rollout의 Worker 이력 증거를 확인할 수 없습니다.')).toBeInTheDocument();
    expect(screen.getByText(/0명으로 해석하지 마세요/)).toBeInTheDocument();
  });
});
