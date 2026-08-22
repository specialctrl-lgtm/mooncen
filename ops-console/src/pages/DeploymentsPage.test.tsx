import '@testing-library/jest-dom/vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { opsApi } from '../api';
import { OpsProvider } from '../context';
import DeploymentsPage from './DeploymentsPage';

vi.mock('../api', () => ({
  opsApi: vi.fn(),
  opsStreamUrl: vi.fn((path: string) => path),
}));

const mockedOpsApi = vi.mocked(opsApi);
let cleanSnapshot = true;
let readinessPayloadOverride: Record<string, unknown> | null = null;
let containerReadinessPayloadOverride: Record<string, unknown> | null = null;

function renderPage(initialEntry = '/deployments') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const rendered = render(
    <QueryClientProvider client={queryClient}>
      <OpsProvider
        session={{
          user: { id: 'admin-id', email: 'admin@example.test', name: '관리자' },
          role: 'admin',
          environment: 'development',
        }}
      >
        <MemoryRouter initialEntries={[initialEntry]}>
          <Routes>
            <Route path="/deployments" element={<DeploymentsPage />} />
            <Route path="/deployments/:id" element={<DeploymentsPage />} />
          </Routes>
        </MemoryRouter>
      </OpsProvider>
    </QueryClientProvider>,
  );
  return { ...rendered, queryClient };
}

describe('DeploymentsPage reviewed deployment', () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    cleanSnapshot = true;
    readinessPayloadOverride = null;
    containerReadinessPayloadOverride = null;
    mockedOpsApi.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === '/services?environment=production') {
        return {
          available: true,
          total: 1,
          limit: 100,
          offset: 0,
          items: [
            {
              id: 'service-id',
              service_name: 'PostgreSQL',
              service_type: 'database',
              environment: 'production',
              service_host: 'cloud',
              topology_host: 'cloud',
              reporter_hostname: 'DEV-SERVER',
              status: 'healthy',
            },
          ],
        };
      }
      if (path === '/deployments?limit=100') {
        return { available: true, total: 0, limit: 100, offset: 0, items: [] };
      }
      if (path === '/deployments/readiness') {
        if (readinessPayloadOverride) return readinessPayloadOverride;
        return {
          available: true,
          can_deploy: false,
          default_target: 'cloud',
          targets: [
            {
              name: 'cloud',
              server: 'cloud',
              domain: 'mooncen.kr',
              remote_dir: '/opt/mooncen',
              role: 'primary',
              deploy_profile: 'full-stack',
              active: true,
              target_identity: 'a'.repeat(64),
              key_ready: true,
              services: [
                { service: 'frontend', role: 'primary' },
                { service: 'backend', role: 'primary' },
                { service: 'database', role: 'primary' },
              ],
            },
            {
              name: 'gen1crawler',
              server: 'gen1crawler',
              domain: 'gen1crawler',
              remote_dir: '/opt/mooncen',
              role: 'crawler',
              deploy_profile: 'crawler-only',
              active: false,
              target_identity: 'b'.repeat(64),
              key_ready: true,
              services: [{ service: 'crawler', role: 'primary' }],
            },
            {
              name: 'gen1db',
              server: 'gen1db',
              domain: 'gen1db',
              remote_dir: '/opt/mooncen',
              role: 'crawler-control',
              deploy_profile: 'control-only',
              active: false,
              target_identity: 'c'.repeat(64),
              key_ready: false,
              services: [
                { service: 'staging_database', role: 'primary' },
                { service: 'crawler_control', role: 'primary' },
              ],
            },
          ],
          topology: {
            crawler_mode: 'legacy',
            crawler_workers: [
              {
                worker_key: 'wtr-linux',
                topology_node: 'wtr-linux',
                dns_host: 'wtr-linux',
                kernel_hostname: 'sgm-standard-pc-i440fx-piix-1996',
                canary: true,
                rollout_order: 1,
                enabled: false,
                concurrency: 1,
                memory_high: '4G',
                memory_max: '6G',
                cpu_quota: '300%',
              },
              {
                worker_key: 'gen1crawler',
                topology_node: 'gen1crawler',
                dns_host: 'gen1crawler',
                kernel_hostname: 'gen1crawler',
                canary: false,
                rollout_order: 2,
                enabled: false,
                concurrency: 1,
                memory_high: '2G',
                memory_max: '4G',
                cpu_quota: '200%',
              },
            ],
          },
          snapshot: {
            branch: 'master',
            commit: '1'.repeat(40),
            short_commit: '1'.repeat(12),
            clean: cleanSnapshot,
            changed_count: cleanSnapshot ? 0 : 7,
            changed_paths: [],
            changed_paths_truncated: false,
            source_tree: '2'.repeat(40),
            short_source_tree: '2'.repeat(12),
            deploy_path_count: 1450,
            excluded_count: 39,
            excluded_paths: [],
            excluded_paths_truncated: false,
          },
          agent: {
            id: 'agent-id',
            name: 'local agent',
            hostname: 'DEV-SERVER',
            status: 'healthy',
            last_seen_at: '2026-07-27T12:00:00+09:00',
          },
          reasons: [
            {
              code: 'native_deployment_operator_only',
              message: '네이티브 배포는 an2p 신뢰 운영자의 Tailscale 대화형 경로만 허용됩니다.',
            },
          ],
        };
      }
      if (path === '/deployments/container/readiness') {
        if (containerReadinessPayloadOverride) return containerReadinessPayloadOverride;
        return {
          available: true,
          executor_supported: false,
          remote_claim_fencing_ready: false,
          pipeline_state: 'evidence_only',
          display_name: 'Docker 불변 이미지 배포',
          default_target: 'cloud',
          targets: [
            {
              name: 'cloud',
              server: 'cloud',
              domain: 'mooncen.kr',
              remote_dir: '/opt/mooncen',
              role: 'primary',
              deploy_profile: 'full-stack',
              active: true,
              target_identity: 'a'.repeat(64),
              key_ready: true,
              services: [],
            },
          ],
          development_target: {
            target: 'an2p-dev',
            target_identity: '8'.repeat(64),
          },
          latest_release: null,
          validation_receipt: null,
          promotion_approval: null,
          target_states: [],
          promotion_evidence_ready: false,
          approval_evidence_ready: false,
          rollback_evidence_ready: false,
          native_rollback_evidence_ready: false,
          can_promote: false,
          can_rollback: false,
          can_rollback_native: false,
          actions: {
            build: { supported: false, can_request: false, evidence_ready: false, blocker_code: 'container_executor_not_implemented' },
            validate: { supported: false, can_request: false, evidence_ready: false, blocker_code: 'container_executor_not_implemented' },
            promote: { supported: false, can_request: false, evidence_ready: false, blocker_code: 'container_executor_not_implemented', required_confirmation: null },
            rollback: { supported: false, can_request: false, evidence_ready: false, blocker_code: 'container_executor_not_implemented', required_confirmation: null },
            rollback_native: { supported: false, can_request: false, evidence_ready: false, blocker_code: 'container_executor_not_implemented', required_confirmation: null },
          },
          reasons: [
            {
              code: 'container_executor_not_implemented',
              message: 'Docker 배포 실행 worker는 아직 연결되지 않았습니다. 이 화면은 증적만 조회합니다.',
            },
          ],
        };
      }
      if (path === '/deployments/container/timeline?limit=100') {
        return { available: true, items: [] };
      }
      if (
        [
          '/deployments/container/actions/promote',
          '/deployments/container/actions/rollback',
          '/deployments/container/actions/rollback-native',
        ].includes(path) &&
        init?.method === 'POST'
      ) {
        return {
          job: { id: 'job-id', status: 'queued' },
          deployment: {
            id: '11111111-1111-4111-8111-111111111111',
            job_id: 'job-id',
            service_type: 'full',
            deployment_mode: 'container',
            deployment_status: 'queued',
          },
          approval: { id: 'approval-id' },
        };
      }
      if (path === '/deployments' && init?.method === 'POST') {
        return {
          job: { id: 'job-id', status: 'queued' },
          deployment: {
            id: '11111111-1111-4111-8111-111111111111',
            job_id: 'job-id',
            service_type: 'full',
            deployment_status: 'queued',
          },
        };
      }
      if (path === '/deployments/11111111-1111-4111-8111-111111111111') {
        return {
          id: '11111111-1111-4111-8111-111111111111',
          job_id: 'job-id',
          target: 'cloud',
          deployment_status: 'queued',
          job_status: 'queued',
        };
      }
      if (path === '/jobs/job-id/logs?limit=1000&tail=true') {
        return { available: true, items: [] };
      }
      throw new Error(`Unexpected API path: ${path}`);
    });
  });

  it('shows gen1db as the reviewed crawler control and staging database owner', async () => {
    renderPage();

    expect((await screen.findAllByText('gen1db')).length).toBeGreaterThan(0);
    expect(screen.getByText(/Staging DB.*Crawler Control|Crawler Control.*Staging DB/)).toBeInTheDocument();
  });

  it('shows the disabled desired crawler fleet separately from observed state', async () => {
    renderPage();

    expect(await screen.findByText('분산 크롤러 워커')).toBeInTheDocument();
    expect(screen.getByText('sgm-standard-pc-i440fx-piix-1996')).toBeInTheDocument();
    expect(screen.getAllByText(/비활성.*인벤토리 준비/)).toHaveLength(2);
    expect(screen.getAllByText(/차단.*NOT READY/)).toHaveLength(2);
    expect(screen.getAllByText('관측 정보 없음')).toHaveLength(2);
    expect(screen.getByText(/concurrency 1.*high 4G.*max 6G.*CPU 300%/)).toBeInTheDocument();
  });

  it('shows concrete deployment blockers when the source snapshot is unavailable', async () => {
    readinessPayloadOverride = {
      available: false,
      can_deploy: false,
      default_target: null,
      targets: [],
      snapshot: null,
      agent: null,
      reasons: [
        {
          code: 'target_registry_invalid',
          message: 'reviewed deployment target registry is unavailable',
        },
        {
          code: 'deployment_agent_offline',
          message: 'deployment worker is offline',
        },
      ],
    };

    renderPage();

    const blockers = await screen.findByRole('status', { name: 'Deployment blockers' });
    expect(blockers).toHaveTextContent('reviewed deployment target registry is unavailable');
    expect(blockers).toHaveTextContent('deployment worker is offline');
    expect(document.querySelector('button.primary')).toBeDisabled();
    expect(document.querySelector('.deploy-ready')).not.toBeInTheDocument();
  });

  it('keeps legacy native deployment out of the long-lived Ops worker', async () => {
    renderPage();

    const deployButton = await screen.findByRole('button', { name: '네이티브 배포(운영자 전용)' });
    expect(deployButton).toBeDisabled();
    expect(screen.getAllByText(/Tailscale 대화형 경로/).length).toBeGreaterThan(0);
    expect(mockedOpsApi).not.toHaveBeenCalledWith('/deployments', expect.anything());
  });

  it('includes dirty development files in the immutable snapshot', async () => {
    cleanSnapshot = false;
    renderPage();

    expect(await screen.findByText('변경 7개 포함')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '네이티브 배포(운영자 전용)' })).toBeDisabled();
  });

  it('separates the production service location from the reporting agent', async () => {
    renderPage();

    expect(await screen.findByRole('columnheader', { name: '설정 소유자' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '상태 확인 Endpoint' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '상태 보고 Agent' })).toBeInTheDocument();
    expect(screen.getAllByRole('cell', { name: 'cloud' })).toHaveLength(2);
    expect(screen.getByRole('cell', { name: 'DEV-SERVER' })).toBeInTheDocument();
    expect(screen.getByText('Web · API · DB primary')).toBeInTheDocument();
  });

  it('keeps database polling as a fallback when the deployment event stream stalls', async () => {
    const deploymentId = '11111111-1111-4111-8111-111111111111';
    const { queryClient } = renderPage(`/deployments/${deploymentId}`);

    await waitFor(() =>
      expect(mockedOpsApi).toHaveBeenCalledWith(`/deployments/${deploymentId}`),
    );
    await waitFor(() =>
      expect(mockedOpsApi).toHaveBeenCalledWith('/jobs/job-id/logs?limit=1000&tail=true'),
    );

    const detailQuery = queryClient.getQueryCache().find({
      queryKey: ['deployment', deploymentId],
      exact: true,
    });
    const logsQuery = queryClient.getQueryCache().find({
      queryKey: ['deployment-logs', 'job-id'],
      exact: true,
    });
    expect(
      (detailQuery?.options as { refetchInterval?: number } | undefined)?.refetchInterval,
    ).toBe(5_000);
    expect(
      (logsQuery?.options as { refetchInterval?: number } | undefined)?.refetchInterval,
    ).toBe(5_000);
  });

  it('labels the old path as native and blocks Docker promotion without the exact pass receipt', async () => {
    renderPage();

    expect(await screen.findByText('Docker 배포 파이프라인')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '네이티브 배포(운영자 전용)' })).toBeDisabled();
    expect(screen.getByText('네이티브 개발 스냅샷 (레거시)')).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: '운영 승격' })).toBeDisabled();
    expect(await screen.findByText(/정확히 일치하는 유효한 an2p-dev PASS 영수증/)).toBeInTheDocument();
  });

  it('shows immutable image, bundle, receipt, current, and previous digest evidence', async () => {
    const releaseDigest = 'd'.repeat(64);
    const previousDigest = 'e'.repeat(64);
    const receiptDigest = 'f'.repeat(64);
    containerReadinessPayloadOverride = {
      available: true,
      executor_supported: false,
      remote_claim_fencing_ready: false,
      pipeline_state: 'evidence_only',
      display_name: 'Docker 불변 이미지 배포',
      default_target: 'cloud',
      targets: [
        {
          name: 'cloud',
          server: 'cloud',
          domain: 'mooncen.kr',
          remote_dir: '/opt/mooncen',
          role: 'primary',
          deploy_profile: 'full-stack',
          active: true,
          target_identity: 'a'.repeat(64),
          key_ready: true,
          services: [],
        },
      ],
      development_target: { target: 'an2p-dev', target_identity: '8'.repeat(64) },
      latest_release: {
        id: 'release-id',
        release_digest: releaseDigest,
        base_commit: '1'.repeat(40),
        source_tree: '2'.repeat(40),
        snapshot_commit: '3'.repeat(40),
        platform: 'linux/amd64',
        api_image_digest: `sha256:${'4'.repeat(64)}`,
        frontend_image_digest: `sha256:${'5'.repeat(64)}`,
        bundle_sha256: '6'.repeat(64),
        compose_sha256: '7'.repeat(64),
        built_at: '2026-08-19T12:00:00Z',
      },
      validation_receipt: {
        id: 'receipt-id',
        receipt_digest: receiptDigest,
        release_id: 'release-id',
        release_digest: releaseDigest,
        target: 'an2p-dev',
        target_identity: '8'.repeat(64),
        status: 'passed',
        validated_at: '2026-08-19T12:10:00Z',
        expires_at: '2026-08-20T12:10:00Z',
      },
      promotion_approval: null,
      target_states: [
        {
          target_identity: 'a'.repeat(64),
          target: 'cloud',
          deployment_id: 'deployment-id',
          current_release_id: 'release-id',
          current_release_digest: releaseDigest,
          previous_release_id: 'previous-id',
          previous_release_digest: previousDigest,
          api_image_digest: `sha256:${'4'.repeat(64)}`,
          frontend_image_digest: `sha256:${'5'.repeat(64)}`,
          bundle_sha256: '6'.repeat(64),
          validation_receipt_id: 'receipt-id',
          validation_receipt_digest: receiptDigest,
          deployment_action: 'promote',
          finished_at: '2026-08-19T13:00:00Z',
        },
      ],
      promotion_evidence_ready: true,
      approval_evidence_ready: false,
      rollback_evidence_ready: true,
      native_rollback_evidence_ready: false,
      can_promote: false,
      can_rollback: false,
      can_rollback_native: false,
      actions: {
        build: { supported: false, can_request: false, evidence_ready: true, blocker_code: 'container_executor_not_implemented' },
        validate: { supported: false, can_request: false, evidence_ready: true, blocker_code: 'container_executor_not_implemented' },
        promote: {
          supported: false,
          can_request: false,
          evidence_ready: true,
          blocker_code: 'container_executor_not_implemented',
          required_confirmation: `PROMOTE ${'a'.repeat(64)} ${releaseDigest} ${receiptDigest}`,
        },
        rollback: {
          supported: false,
          can_request: false,
          evidence_ready: true,
          blocker_code: 'container_executor_not_implemented',
          required_confirmation: `ROLLBACK ${'a'.repeat(64)} ${releaseDigest} ${previousDigest}`,
        },
        rollback_native: {
          supported: false,
          can_request: false,
          evidence_ready: false,
          blocker_code: 'container_executor_not_implemented',
          required_confirmation: null,
        },
      },
      reasons: [{ code: 'container_executor_not_implemented', message: '실행 worker 미연결' }],
    };

    renderPage();

    expect((await screen.findAllByText('PASS')).length).toBeGreaterThan(0);
    expect(screen.getAllByText(releaseDigest.slice(0, 12)).length).toBeGreaterThan(0);
    expect(screen.getByText(previousDigest.slice(0, 12))).toBeInTheDocument();
    expect(screen.getAllByText(receiptDigest.slice(0, 12)).length).toBeGreaterThan(0);
    expect(screen.getByText(`sha256:${'4'.repeat(12)}`)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '운영 승격' })).toBeDisabled();
  });

  it('blocks native maintenance when the live baseline identity is absent', async () => {
    const currentDigest = 'c'.repeat(64);
    const targetIdentity = 'a'.repeat(64);
    containerReadinessPayloadOverride = {
      available: true,
      executor_supported: true,
      remote_claim_fencing_ready: true,
      pipeline_state: 'ready',
      display_name: 'Docker 불변 이미지 배포',
      default_target: 'cloud',
      agent: { id: 'agent-id', name: 'an2p worker', hostname: 'an2p', status: 'healthy', last_seen_at: '2026-08-19T12:00:00Z' },
      targets: [{ name: 'cloud', server: 'cloud', domain: 'mooncen.kr', remote_dir: '/opt/mooncen', role: 'primary', deploy_profile: 'full-stack', environment: 'production', active: true, target_identity: targetIdentity, key_ready: true, services: [] }],
      development_target: { target: 'an2p-dev', target_identity: '8'.repeat(64) },
      latest_release: null,
      validation_receipt: null,
      promotion_approval: null,
      target_states: [{
        target_identity: targetIdentity, target: 'cloud', deployment_id: 'current-deployment',
        current_release_id: 'current-release', current_release_digest: currentDigest,
        previous_release_id: null, previous_release_digest: null,
        api_image_digest: `sha256:${'9'.repeat(64)}`, frontend_image_digest: `sha256:${'0'.repeat(64)}`,
        bundle_sha256: 'f'.repeat(64), validation_receipt_id: null,
        validation_receipt_digest: null, deployment_action: 'promote', finished_at: '2026-08-19T11:00:00Z',
      }],
      promotion_evidence_ready: false,
      approval_evidence_ready: false,
      rollback_evidence_ready: false,
      native_rollback_evidence_ready: false,
      can_promote: false,
      can_rollback: false,
      can_rollback_native: false,
      actions: {
        build: { supported: false, can_request: false, evidence_ready: false, blocker_code: 'container_build_validate_executor_unavailable' },
        validate: { supported: false, can_request: false, evidence_ready: false, blocker_code: 'container_build_validate_executor_unavailable' },
        promote: { supported: true, can_request: false, evidence_ready: false, blocker_code: 'container_promotion_not_ready', required_confirmation: null },
        rollback: { supported: true, can_request: false, evidence_ready: false, blocker_code: 'container_native_rollback_breakglass_only', required_confirmation: null },
        rollback_native: { supported: true, can_request: false, evidence_ready: false, blocker_code: 'container_native_maintenance_not_ready', required_confirmation: null },
      },
      reasons: [{ code: 'container_native_rollback_breakglass_only', message: 'native break-glass only' }],
    };

    renderPage();

    expect(await screen.findByText(/활성 Docker 상태와 controller에 고정된 native baseline identity/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '이전 digest로 롤백' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Native maintenance 전환' })).toBeDisabled();
  });

  it('queues promotion only after the exact PASS receipt confirmation', async () => {
    const releaseDigest = 'd'.repeat(64);
    const receiptDigest = 'e'.repeat(64);
    const targetIdentity = 'a'.repeat(64);
    const currentDigest = 'c'.repeat(64);
    const previousDigest = 'b'.repeat(64);
    const runtimeGeneration = 7;
    const controllerStateHash = 'f'.repeat(64);
    const promotionConfirmation = `PROMOTE ${targetIdentity} ${releaseDigest} ${receiptDigest} ${runtimeGeneration} ${controllerStateHash}`;
    containerReadinessPayloadOverride = {
      available: true,
      executor_supported: true,
      remote_claim_fencing_ready: true,
      pipeline_state: 'ready',
      display_name: 'Docker 불변 이미지 배포',
      default_target: 'cloud',
      agent: { id: 'agent-id', name: 'an2p worker', hostname: 'an2p', status: 'healthy', last_seen_at: '2026-08-19T12:00:00Z' },
      targets: [{
        name: 'cloud', server: 'cloud', domain: 'mooncen.kr', remote_dir: '/opt/mooncen',
        role: 'primary', deploy_profile: 'full-stack', environment: 'production', active: true,
        target_identity: targetIdentity, key_ready: true, services: [],
      }],
      development_target: { target: 'an2p-dev', target_identity: '8'.repeat(64) },
      latest_release: {
        id: 'release-id', release_digest: releaseDigest, base_commit: '1'.repeat(40),
        source_tree: '2'.repeat(40), snapshot_commit: '3'.repeat(40), platform: 'linux/amd64',
        api_image_digest: `sha256:${'4'.repeat(64)}`, frontend_image_digest: `sha256:${'5'.repeat(64)}`,
        bundle_sha256: '6'.repeat(64), compose_sha256: '7'.repeat(64), built_at: '2026-08-19T12:00:00Z',
      },
      validation_receipt: {
        id: 'receipt-id', receipt_digest: receiptDigest, release_id: 'release-id', release_digest: releaseDigest,
        target: 'an2p-dev', target_identity: '8'.repeat(64), status: 'passed',
        validated_at: '2026-08-19T12:10:00Z', expires_at: '2026-08-20T12:10:00Z',
      },
      promotion_approval: null,
      live_runtime_cas: {
        expected_runtime_generation: runtimeGeneration,
        expected_controller_state_sha256: controllerStateHash,
        expected_active_release_digest: currentDigest,
        expected_previous_release_digest: previousDigest,
      },
      target_states: [{
        target_identity: targetIdentity, target: 'cloud', deployment_id: 'current-deployment',
        current_release_id: 'current-release', current_release_digest: currentDigest,
        previous_release_id: 'previous-release', previous_release_digest: previousDigest,
        api_image_digest: `sha256:${'9'.repeat(64)}`, frontend_image_digest: `sha256:${'0'.repeat(64)}`,
        bundle_sha256: 'f'.repeat(64), validation_receipt_id: 'old-receipt',
        validation_receipt_digest: '1'.repeat(64), deployment_action: 'promote', finished_at: '2026-08-19T11:00:00Z',
      }],
      promotion_evidence_ready: true,
      approval_evidence_ready: false,
      rollback_evidence_ready: true,
      native_rollback_evidence_ready: false,
      can_promote: true,
      can_rollback: true,
      can_rollback_native: false,
      actions: {
        build: { supported: false, can_request: false, evidence_ready: true, blocker_code: 'container_build_validate_executor_unavailable' },
        validate: { supported: false, can_request: false, evidence_ready: true, blocker_code: 'container_build_validate_executor_unavailable' },
        promote: { supported: true, can_request: true, evidence_ready: true, blocker_code: null, required_confirmation: promotionConfirmation },
        rollback: { supported: true, can_request: true, evidence_ready: true, blocker_code: null, required_confirmation: `ROLLBACK ${targetIdentity} ${currentDigest} ${previousDigest} ${runtimeGeneration} ${controllerStateHash}` },
        rollback_native: { supported: true, can_request: false, evidence_ready: false, blocker_code: 'container_native_maintenance_not_ready', required_confirmation: null },
      },
      reasons: [],
    };

    renderPage();

    const promote = await screen.findByRole('button', { name: '운영 승격' });
    expect(screen.getByText('epoch 고정됨')).toBeInTheDocument();
    expect(promote).toBeEnabled();
    fireEvent.click(promote);
    fireEvent.change(await screen.findByLabelText('Docker 배포 사유'), { target: { value: 'reviewed PASS receipt' } });
    fireEvent.change(screen.getByLabelText('Docker 확인 문구'), { target: { value: promotionConfirmation } });
    fireEvent.click(screen.getByRole('button', { name: 'Docker 운영 승격 시작' }));

    await waitFor(() => expect(mockedOpsApi).toHaveBeenCalledWith(
      '/deployments/container/actions/promote',
      {
        method: 'POST',
        body: JSON.stringify({
          target: 'cloud',
          target_environment: 'production',
          target_identity: targetIdentity,
          reason: 'reviewed PASS receipt',
          confirmation: promotionConfirmation,
          expected_runtime_generation: runtimeGeneration,
          expected_controller_state_sha256: controllerStateHash,
          release_digest: releaseDigest,
          validation_receipt_digest: receiptDigest,
        }),
      },
    ));
  });

  it('queues rollback only with the exact current and previous digest confirmation', async () => {
    const releaseDigest = 'd'.repeat(64);
    const currentDigest = 'c'.repeat(64);
    const previousDigest = 'b'.repeat(64);
    const targetIdentity = 'a'.repeat(64);
    const runtimeGeneration = 8;
    const controllerStateHash = 'e'.repeat(64);
    const rollbackConfirmation = `ROLLBACK ${targetIdentity} ${currentDigest} ${previousDigest} ${runtimeGeneration} ${controllerStateHash}`;
    containerReadinessPayloadOverride = {
      available: true,
      executor_supported: true,
      remote_claim_fencing_ready: true,
      pipeline_state: 'ready',
      display_name: 'Docker 불변 이미지 배포',
      default_target: 'cloud',
      agent: { id: 'agent-id', name: 'an2p worker', hostname: 'an2p', status: 'healthy', last_seen_at: '2026-08-19T12:00:00Z' },
      targets: [{ name: 'cloud', server: 'cloud', domain: 'mooncen.kr', remote_dir: '/opt/mooncen', role: 'primary', deploy_profile: 'full-stack', environment: 'production', active: true, target_identity: targetIdentity, key_ready: true, services: [] }],
      development_target: { target: 'an2p-dev', target_identity: '8'.repeat(64) },
      latest_release: { id: 'release-id', release_digest: releaseDigest, base_commit: '1'.repeat(40), source_tree: '2'.repeat(40), snapshot_commit: '3'.repeat(40), platform: 'linux/amd64', api_image_digest: `sha256:${'4'.repeat(64)}`, frontend_image_digest: `sha256:${'5'.repeat(64)}`, bundle_sha256: '6'.repeat(64), compose_sha256: '7'.repeat(64), built_at: '2026-08-19T12:00:00Z' },
      validation_receipt: null,
      promotion_approval: null,
      live_runtime_cas: {
        expected_runtime_generation: runtimeGeneration,
        expected_controller_state_sha256: controllerStateHash,
        expected_active_release_digest: currentDigest,
        expected_previous_release_digest: previousDigest,
      },
      target_states: [{ target_identity: targetIdentity, target: 'cloud', deployment_id: 'current-deployment', current_release_id: 'current-release', current_release_digest: currentDigest, previous_release_id: 'previous-release', previous_release_digest: previousDigest, api_image_digest: `sha256:${'9'.repeat(64)}`, frontend_image_digest: `sha256:${'0'.repeat(64)}`, bundle_sha256: 'f'.repeat(64), validation_receipt_id: null, validation_receipt_digest: null, deployment_action: 'promote', finished_at: '2026-08-19T11:00:00Z' }],
      promotion_evidence_ready: false,
      approval_evidence_ready: false,
      rollback_evidence_ready: true,
      native_rollback_evidence_ready: false,
      can_promote: false,
      can_rollback: true,
      can_rollback_native: false,
      actions: {
        build: { supported: false, can_request: false, evidence_ready: true, blocker_code: 'container_build_validate_executor_unavailable' },
        validate: { supported: false, can_request: false, evidence_ready: true, blocker_code: 'container_build_validate_executor_unavailable' },
        promote: { supported: true, can_request: false, evidence_ready: false, blocker_code: 'container_promotion_not_ready', required_confirmation: null },
        rollback: { supported: true, can_request: true, evidence_ready: true, blocker_code: null, required_confirmation: rollbackConfirmation },
        rollback_native: { supported: true, can_request: false, evidence_ready: false, blocker_code: 'container_native_maintenance_not_ready', required_confirmation: null },
      },
      reasons: [],
    };

    renderPage();

    const rollback = await screen.findByRole('button', { name: '이전 digest로 롤백' });
    expect(rollback).toBeEnabled();
    fireEvent.click(rollback);
    fireEvent.change(await screen.findByLabelText('Docker 배포 사유'), { target: { value: 'restore reviewed previous' } });
    fireEvent.change(screen.getByLabelText('Docker 확인 문구'), { target: { value: rollbackConfirmation } });
    fireEvent.click(screen.getByRole('button', { name: 'Docker 롤백 시작' }));

    await waitFor(() => expect(mockedOpsApi).toHaveBeenCalledWith(
      '/deployments/container/actions/rollback',
      {
        method: 'POST',
        body: JSON.stringify({
          target: 'cloud',
          target_environment: 'production',
          target_identity: targetIdentity,
          reason: 'restore reviewed previous',
          confirmation: rollbackConfirmation,
          expected_runtime_generation: runtimeGeneration,
          expected_controller_state_sha256: controllerStateHash,
          current_release_digest: currentDigest,
          rollback_release_digest: previousDigest,
        }),
      },
    ));
  });

  it('queues native maintenance only with the exact live CAS and baseline confirmation', async () => {
    const currentDigest = 'c'.repeat(64);
    const nativeBaseline = '9'.repeat(64);
    const targetIdentity = 'a'.repeat(64);
    const runtimeGeneration = 9;
    const controllerStateHash = 'e'.repeat(64);
    const nativeConfirmation = `ROLLBACK_NATIVE ${targetIdentity} ${currentDigest} ${nativeBaseline} ${runtimeGeneration} ${controllerStateHash}`;
    containerReadinessPayloadOverride = {
      available: true,
      executor_supported: true,
      remote_claim_fencing_ready: true,
      pipeline_state: 'ready',
      display_name: 'Docker 불변 이미지 배포',
      default_target: 'cloud',
      agent: { id: 'agent-id', name: 'an2p worker', hostname: 'an2p', status: 'healthy', last_seen_at: '2026-08-19T12:00:00Z' },
      targets: [{ name: 'cloud', server: 'cloud', domain: 'mooncen.kr', remote_dir: '/opt/mooncen', role: 'primary', deploy_profile: 'full-stack', environment: 'production', active: true, target_identity: targetIdentity, key_ready: true, services: [] }],
      development_target: { target: 'an2p-dev', target_identity: '8'.repeat(64) },
      latest_release: null,
      validation_receipt: null,
      promotion_approval: null,
      live_runtime_cas: {
        expected_runtime_generation: runtimeGeneration,
        expected_controller_state_sha256: controllerStateHash,
        expected_active_release_digest: currentDigest,
        expected_previous_release_digest: null,
        native_baseline_identity: nativeBaseline,
      },
      target_states: [{
        target_identity: targetIdentity,
        target: 'cloud',
        deployment_id: 'current-deployment',
        runtime_target_kind: 'container',
        runtime_native_baseline_identity: nativeBaseline,
        current_release_id: 'current-release',
        current_release_digest: currentDigest,
        previous_release_id: null,
        previous_release_digest: null,
        api_image_digest: `sha256:${'9'.repeat(64)}`,
        frontend_image_digest: `sha256:${'0'.repeat(64)}`,
        bundle_sha256: 'f'.repeat(64),
        validation_receipt_id: null,
        validation_receipt_digest: null,
        deployment_action: 'promote',
        finished_at: '2026-08-19T11:00:00Z',
      }],
      promotion_evidence_ready: false,
      approval_evidence_ready: false,
      rollback_evidence_ready: false,
      native_rollback_evidence_ready: true,
      can_promote: false,
      can_rollback: false,
      can_rollback_native: true,
      actions: {
        build: { supported: false, can_request: false, evidence_ready: false, blocker_code: 'container_build_validate_executor_unavailable' },
        validate: { supported: false, can_request: false, evidence_ready: false, blocker_code: 'container_build_validate_executor_unavailable' },
        promote: { supported: true, can_request: false, evidence_ready: false, blocker_code: 'container_promotion_not_ready', required_confirmation: null },
        rollback: { supported: true, can_request: false, evidence_ready: false, blocker_code: 'container_rollback_previous_release_unavailable', required_confirmation: null },
        rollback_native: { supported: true, can_request: true, evidence_ready: true, blocker_code: null, required_confirmation: nativeConfirmation, native_baseline_identity: nativeBaseline },
      },
      reasons: [],
    };

    renderPage();

    const nativeMaintenance = await screen.findByRole('button', { name: 'Native maintenance 전환' });
    expect(nativeMaintenance).toBeEnabled();
    fireEvent.click(nativeMaintenance);
    fireEvent.change(await screen.findByLabelText('Docker 배포 사유'), { target: { value: 'reviewed native maintenance' } });
    const confirmation = screen.getByLabelText('Docker 확인 문구');
    fireEvent.change(confirmation, { target: { value: `${nativeConfirmation} mismatch` } });
    expect(screen.getByRole('button', { name: 'Native maintenance 시작' })).toBeDisabled();
    fireEvent.change(confirmation, { target: { value: nativeConfirmation } });
    const submit = screen.getByRole('button', { name: 'Native maintenance 시작' });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);

    await waitFor(() => expect(mockedOpsApi).toHaveBeenCalledWith(
      '/deployments/container/actions/rollback-native',
      {
        method: 'POST',
        body: JSON.stringify({
          target: 'cloud',
          target_environment: 'production',
          target_identity: targetIdentity,
          reason: 'reviewed native maintenance',
          confirmation: nativeConfirmation,
          expected_runtime_generation: runtimeGeneration,
          expected_controller_state_sha256: controllerStateHash,
          current_release_digest: currentDigest,
          native_baseline_identity: nativeBaseline,
        }),
      },
    ));
  });
});
