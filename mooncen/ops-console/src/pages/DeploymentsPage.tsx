import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { CloudUpload, GitBranch, RefreshCw, Server, ShieldCheck } from 'lucide-react';
import { useNavigate, useParams } from 'react-router';
import { opsApi } from '../api';
import DataTable from '../components/DataTable';
import StatusBadge from '../components/StatusBadge';
import { DefinitionList, DetailPanel, PageHeader, QueryState, StreamStatus } from '../components/Ui';
import { useOpsSession } from '../context';
import { appendStreamLog, useJobEventStream } from '../hooks/useJobEventStream';
import type { OpsService, PageResponse } from '../types';
import { formatDate } from '../utils';

type Deployment = Record<string, unknown> & {
  id: string;
  job_id: string;
  service_type: string;
  deployment_status: string;
};

type ContainerRelease = {
  id: string;
  release_digest: string;
  base_commit: string;
  source_tree: string;
  snapshot_commit: string;
  platform: string;
  api_image_digest: string;
  frontend_image_digest: string;
  bundle_sha256: string;
  compose_sha256: string;
  built_at: string;
};

type ContainerValidationReceipt = {
  id: string;
  receipt_digest: string;
  release_id: string;
  release_digest: string;
  target: 'an2p-dev';
  target_identity: string;
  status: 'passed' | 'failed';
  validated_at: string;
  expires_at: string;
};

type ContainerTargetState = {
  target_identity: string;
  target: string;
  deployment_id: string;
  runtime_target_kind: 'container' | 'native';
  runtime_native_baseline_identity: string;
  current_release_id?: string | null;
  current_release_digest?: string | null;
  previous_release_id?: string | null;
  previous_release_digest?: string | null;
  api_image_digest: string;
  frontend_image_digest: string;
  bundle_sha256: string;
  validation_receipt_id?: string | null;
  validation_receipt_digest?: string | null;
  deployment_action: 'promote' | 'rollback' | 'rollback_native';
  finished_at?: string | null;
};

type ContainerActionReadiness = {
  supported: boolean;
  can_request: boolean;
  evidence_ready: boolean;
  blocker_code: string | null;
  required_confirmation?: string | null;
  approval_ready?: boolean;
  native_baseline_identity?: string | null;
};

type ContainerDeployReadiness = {
  available: boolean;
  executor_supported: boolean;
  remote_claim_fencing_ready: boolean;
  pipeline_state: 'blocked' | 'ready';
  display_name: string;
  default_target: string | null;
  targets: DeployTarget[];
  development_target: {
    target: 'an2p-dev';
    target_identity: string | null;
  };
  agent: {
    id: string;
    name: string;
    hostname: string;
    status: string;
    last_seen_at: string;
  } | null;
  latest_release: ContainerRelease | null;
  validation_receipt: ContainerValidationReceipt | null;
  promotion_approval: {
    id: string;
    approved_at: string;
    expires_at: string;
  } | null;
  target_states: ContainerTargetState[];
  live_runtime_cas: {
    expected_runtime_generation: number;
    expected_controller_state_sha256: string;
    expected_active_release_digest: string | null;
    expected_previous_release_digest: string | null;
    native_baseline_identity: string | null;
  } | null;
  promotion_evidence_ready: boolean;
  approval_evidence_ready: boolean;
  rollback_evidence_ready: boolean;
  native_rollback_evidence_ready: boolean;
  can_promote: boolean;
  can_rollback: boolean;
  can_rollback_native: boolean;
  actions: Record<'build' | 'validate' | 'promote' | 'rollback' | 'rollback_native', ContainerActionReadiness>;
  reasons: Array<{ code: string; message: string }>;
};

type ContainerTimelineItem = Record<string, unknown> & {
  id: string;
  deployment_action: 'promote' | 'rollback' | 'rollback_native';
  target: string;
  container_release_digest: string;
  previous_container_release_digest?: string | null;
  validation_status?: 'passed' | 'failed' | null;
  validation_receipt_digest?: string | null;
  deployment_status: string;
  runtime_generation?: number | null;
  activated_release_digest?: string | null;
  runtime_previous_release_digest?: string | null;
  controller_state_sha256?: string | null;
  runtime_target_kind?: 'container' | 'native' | null;
  runtime_native_baseline_identity?: string | null;
  created_at: string;
};

type DeployTarget = {
  name: string;
  server: string;
  domain: string;
  remote_dir: string;
  role: 'primary' | 'standby' | 'crawler' | 'crawler-control';
  deploy_profile?: 'full-stack' | 'crawler-only' | 'control-only';
  active: boolean;
  target_identity: string;
  environment?: string;
  key_ready: boolean;
  services: Array<{ service: string; role: string; replicates_from?: string | null }>;
};

const serviceLabels: Record<string, string> = {
  frontend: 'Web',
  backend: 'API',
  database: 'DB',
  staging_database: 'Staging DB',
  crawler: 'Crawler',
  crawler_control: 'Crawler Control',
};

function targetServiceSummary(target: DeployTarget): string {
  if (!target.services?.length) return '-';
  return target.services
    .map(({ service, role }) => {
      const label = serviceLabels[service] || service;
      return service === 'database' ? `${label} ${role}` : label;
    })
    .join(' · ');
}

function shortDigest(value: unknown, length = 12): string {
  const text = String(value || '').trim();
  return text ? text.slice(0, length) : '-';
}

type DeployReadiness = {
  available: boolean;
  can_deploy: boolean;
  default_target: string | null;
  targets: DeployTarget[];
  topology?: {
    crawler_mode: 'legacy' | 'distributed';
    crawler_workers: Array<{
      worker_key: string;
      topology_node: string;
      dns_host: string;
      kernel_hostname: string;
      canary: boolean;
      rollout_order: number;
      enabled: boolean;
      concurrency: number;
      memory_high: string;
      memory_max: string;
      cpu_quota: string;
    }>;
  } | null;
  snapshot: {
    branch: string;
    commit: string;
    short_commit: string;
    clean: boolean;
    changed_count: number;
    changed_paths: string[];
    changed_paths_truncated: boolean;
    source_tree: string;
    short_source_tree: string;
    deploy_path_count: number;
    excluded_count: number;
    excluded_paths: string[];
    excluded_paths_truncated: boolean;
  } | null;
  agent: {
    id: string;
    name: string;
    hostname: string;
    status: string;
    last_seen_at: string;
  } | null;
  reasons: Array<{ code: string; message: string }>;
};

export default function DeploymentsPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const session = useOpsSession();
  const queryClient = useQueryClient();
  const [showDeploy, setShowDeploy] = useState(false);
  const [selectedTarget, setSelectedTarget] = useState('');
  const [skipWorkers, setSkipWorkers] = useState(false);
  const [confirmation, setConfirmation] = useState('');
  const [containerAction, setContainerAction] = useState<'promote' | 'rollback' | 'rollback_native' | null>(null);
  const [containerReason, setContainerReason] = useState('');
  const [containerConfirmation, setContainerConfirmation] = useState('');

  const current = useQuery({
    queryKey: ['deployment-services'],
    queryFn: () => opsApi<PageResponse<OpsService>>('/services?environment=production'),
    refetchInterval: 30_000,
  });
  const deployments = useQuery({
    queryKey: ['deployments'],
    queryFn: () => opsApi<PageResponse<Deployment>>('/deployments?limit=100'),
    refetchInterval: 15_000,
  });
  const readiness = useQuery({
    queryKey: ['deployment-readiness'],
    queryFn: () => opsApi<DeployReadiness>('/deployments/readiness'),
    refetchInterval: 15_000,
  });
  const containerReadiness = useQuery({
    queryKey: ['container-deployment-readiness'],
    queryFn: () => opsApi<ContainerDeployReadiness>('/deployments/container/readiness'),
    refetchInterval: 15_000,
  });
  const containerTimeline = useQuery({
    queryKey: ['container-deployment-timeline'],
    queryFn: () =>
      opsApi<{ available: boolean; items: ContainerTimelineItem[] }>(
        '/deployments/container/timeline?limit=100',
      ),
    refetchInterval: 15_000,
  });
  const detail = useQuery({
    queryKey: ['deployment', id],
    queryFn: () => opsApi<Record<string, unknown>>(`/deployments/${id}`),
    enabled: Boolean(id),
    // SSE is the low-latency path, but it is not authoritative: proxies,
    // browser sleep, and transient network errors can leave EventSource in a
    // reconnect loop without delivering the terminal event.  Keep a bounded
    // database-backed poll active while the detail panel is open so a job can
    // never remain visually stuck at 1% after the server has already moved on.
    refetchInterval: id ? 5_000 : false,
  });
  const jobId = String(detail.data?.job_id || '');
  const logs = useQuery({
    queryKey: ['deployment-logs', jobId],
    queryFn: () =>
      opsApi<{ available: boolean; items: Array<Record<string, unknown>> }>(
        `/jobs/${jobId}/logs?limit=1000&tail=true`,
      ),
    enabled: Boolean(jobId),
    refetchInterval: jobId ? 5_000 : false,
  });

  useEffect(() => {
    if (!selectedTarget && readiness.data?.default_target) {
      setSelectedTarget(readiness.data.default_target);
    }
  }, [readiness.data?.default_target, selectedTarget]);

  const streamActive = Boolean(jobId && ['queued', 'assigned', 'running'].includes(String(detail.data?.job_status)));
  const stream = useJobEventStream({
    jobId,
    enabled: streamActive,
    onJob: (job) => {
      queryClient.setQueryData<Record<string, unknown>>(['deployment', id], (currentDetail) => (
        currentDetail
          ? {
              ...currentDetail,
              job_status: job.status,
              progress: job.progress,
              error_code: job.error_code,
              error_message: job.error_message,
              finished_at: job.finished_at,
            }
          : currentDetail
      ));
      queryClient.setQueryData<PageResponse<Deployment>>(['deployments'], (currentPage) => (
        currentPage
          ? {
              ...currentPage,
              items: currentPage.items.map((deployment) => (
                deployment.job_id === jobId
                  ? { ...deployment, deployment_status: String(job.status || deployment.deployment_status), progress: job.progress }
                  : deployment
              )),
            }
          : currentPage
      ));
    },
    onLog: (log) => {
      queryClient.setQueryData<{ available: boolean; items: Array<Record<string, unknown>> }>(
        ['deployment-logs', jobId],
        (currentLogs) => appendStreamLog(currentLogs, log),
      );
    },
    onEnd: () => {
      void queryClient.invalidateQueries({ queryKey: ['deployment', id] });
      void queryClient.invalidateQueries({ queryKey: ['deployments'] });
    },
  });

  const selected = readiness.data?.targets.find((target) => target.name === selectedTarget);
  const snapshot = readiness.data?.snapshot;
  const readinessReasons = readiness.data?.reasons ?? [];
  const confirmationText = selected && snapshot ? `DEPLOY ${selected.name} ${snapshot.short_source_tree}` : '';
  const canCreate = Boolean(
    session.role === 'admin' &&
      readiness.data?.can_deploy &&
      selected?.key_ready &&
      snapshot?.source_tree,
  );
  const containerTarget = containerReadiness.data?.targets.find(
    (target) => target.name === containerReadiness.data?.default_target,
  );
  const containerState = containerReadiness.data?.target_states.find(
    (state) => state.target_identity === containerTarget?.target_identity,
  );
  const canPromoteContainer = Boolean(
    session.role === 'admin' &&
      containerReadiness.data?.actions.promote.can_request &&
      containerReadiness.data?.can_promote,
  );
  const canRollbackContainer = Boolean(
    session.role === 'admin' &&
      containerReadiness.data?.actions.rollback.can_request &&
      containerReadiness.data?.can_rollback,
  );
  const canRollbackNative = Boolean(
    session.role === 'admin' &&
      containerReadiness.data?.actions.rollback_native.can_request &&
      containerReadiness.data?.can_rollback_native,
  );

  const createDeployment = useMutation({
    mutationFn: () =>
      opsApi<{ job: Record<string, unknown>; deployment: Deployment }>('/deployments', {
        method: 'POST',
        body: JSON.stringify({
          target: selectedTarget,
          target_commit: snapshot?.commit,
          source_tree: snapshot?.source_tree,
          skip_workers: skipWorkers,
          confirmation,
        }),
      }),
    onSuccess: (result) => {
      setShowDeploy(false);
      setConfirmation('');
      void queryClient.invalidateQueries({ queryKey: ['deployments'] });
      void queryClient.invalidateQueries({ queryKey: ['deployment-readiness'] });
      navigate(`/deployments/${result.deployment.id}`);
    },
  });
  const requestContainerAction = useMutation({
    mutationFn: () => {
      if (!containerAction || !containerTarget || !containerReadiness.data) {
        throw new Error('Docker 배포 요청 증적이 준비되지 않았습니다.');
      }
      const common = {
        target: containerTarget.name,
        target_environment: 'production',
        target_identity: containerTarget.target_identity,
        reason: containerReason.trim(),
        confirmation: containerConfirmation,
        expected_runtime_generation:
          containerReadiness.data.live_runtime_cas?.expected_runtime_generation,
        expected_controller_state_sha256:
          containerReadiness.data.live_runtime_cas?.expected_controller_state_sha256,
      };
      const body = containerAction === 'promote'
        ? {
            ...common,
            release_digest: containerReadiness.data.latest_release?.release_digest,
            validation_receipt_digest: containerReadiness.data.validation_receipt?.receipt_digest,
          }
        : containerAction === 'rollback'
          ? {
            ...common,
            current_release_digest: containerState?.current_release_digest,
            rollback_release_digest: containerState?.previous_release_digest,
          }
          : {
              ...common,
              current_release_digest: containerState?.current_release_digest,
              native_baseline_identity:
                containerReadiness.data.live_runtime_cas?.native_baseline_identity,
            };
      const actionPath = containerAction === 'rollback_native' ? 'rollback-native' : containerAction;
      return opsApi<{ job: Record<string, unknown>; deployment: Deployment; approval: Record<string, unknown> }>(
        `/deployments/container/actions/${actionPath}`,
        { method: 'POST', body: JSON.stringify(body) },
      );
    },
    onSuccess: (result) => {
      setContainerAction(null);
      setContainerReason('');
      setContainerConfirmation('');
      void queryClient.invalidateQueries({ queryKey: ['deployments'] });
      void queryClient.invalidateQueries({ queryKey: ['container-deployment-readiness'] });
      void queryClient.invalidateQueries({ queryKey: ['container-deployment-timeline'] });
      navigate(`/deployments/${result.deployment.id}`);
    },
  });
  const cancelDeployment = useMutation({
    mutationFn: () =>
      opsApi(`/jobs/${jobId}/cancel`, {
        method: 'POST',
        body: JSON.stringify({ reason: 'Deployments 화면에서 배포 취소 요청' }),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['deployment', id] });
      void queryClient.invalidateQueries({ queryKey: ['deployments'] });
    },
  });

  const columns = useMemo<ColumnDef<Deployment>[]>(
    () => [
      { accessorKey: 'target', header: '대상', cell: ({ row }) => String(row.original.target || '-') },
      {
        accessorKey: 'deployment_mode',
        header: '방식',
        cell: ({ row }) => (row.original.deployment_mode === 'container' ? 'Docker' : '네이티브(레거시)'),
      },
      { accessorKey: 'service_type', header: '범위' },
      { accessorKey: 'target_version', header: '버전', cell: ({ row }) => String(row.original.target_version || '-') },
      { accessorKey: 'target_commit', header: 'Commit', cell: ({ row }) => String(row.original.target_commit || '-').slice(0, 12) },
      { accessorKey: 'deployment_status', header: '상태', cell: ({ row }) => <StatusBadge status={row.original.deployment_status} /> },
      { accessorKey: 'progress', header: '진행률', cell: ({ row }) => `${Number(row.original.progress || 0)}%` },
      { accessorKey: 'started_at', header: '시작', cell: ({ row }) => formatDate(row.original.started_at) },
      { accessorKey: 'finished_at', header: '종료', cell: ({ row }) => formatDate(row.original.finished_at) },
    ],
    [],
  );
  const currentColumns = useMemo<ColumnDef<OpsService>[]>(
    () => [
      { accessorKey: 'service_name', header: '서비스' },
      { accessorKey: 'service_type', header: '종류' },
      {
        accessorKey: 'configured_owner_host',
        header: '설정 소유자',
        cell: ({ row }) => String(row.original.configured_owner_host || row.original.topology_host || '-'),
      },
      {
        accessorKey: 'service_host',
        header: '상태 확인 Endpoint',
        cell: ({ row }) => String(row.original.service_host || '-'),
      },
      {
        accessorKey: 'reporter_hostname',
        header: '상태 보고 Agent',
        cell: ({ row }) => String(row.original.reporter_hostname || '-'),
      },
      { accessorKey: 'current_version', header: '버전', cell: ({ row }) => String(row.original.current_version || '-') },
      { accessorKey: 'current_commit', header: 'Commit', cell: ({ row }) => String(row.original.current_commit || '-').slice(0, 12) },
      { accessorKey: 'status', header: '상태', cell: ({ row }) => <StatusBadge status={row.original.status} /> },
      { accessorKey: 'last_checked_at', header: '확인', cell: ({ row }) => formatDate(row.original.last_checked_at) },
    ],
    [],
  );

  const refreshAll = () => {
    void queryClient.invalidateQueries({ queryKey: ['deployment-readiness'] });
    void queryClient.invalidateQueries({ queryKey: ['deployment-services'] });
    void queryClient.invalidateQueries({ queryKey: ['deployments'] });
    void queryClient.invalidateQueries({ queryKey: ['container-deployment-readiness'] });
    void queryClient.invalidateQueries({ queryKey: ['container-deployment-timeline'] });
  };

  return (
    <>
      <PageHeader
        eyebrow="IMMUTABLE CONTAINER RELEASE"
        title="Deployments"
        description="동일한 Docker 이미지 bundle을 an2p-dev에서 검증한 뒤 운영에 승격합니다. 네이티브 배포는 an2p 신뢰 운영자의 Tailscale 대화형 경로만 허용합니다."
        actions={
          <>
            <button className="icon-button" type="button" onClick={refreshAll} title="배포 상태 새로고침" aria-label="배포 상태 새로고침">
              <RefreshCw size={17} aria-hidden="true" />
            </button>
            {session.role === 'admin' ? (
              <button
                className="button primary button-with-icon"
                type="button"
                disabled={!canCreate}
                title="Long-lived Ops worker는 네이티브 배포를 실행하지 않습니다."
                onClick={() => {
                  setSelectedTarget(readiness.data?.default_target || readiness.data?.targets[0]?.name || '');
                  setConfirmation('');
                  setShowDeploy(true);
                }}
              >
                <CloudUpload size={17} aria-hidden="true" />
                네이티브 배포(운영자 전용)
              </button>
            ) : null}
          </>
        }
      />

      <section className="panel container-pipeline-panel">
        <header className="section-header">
          <div>
            <h2>Docker 배포 파이프라인</h2>
            <small>Build → an2p-dev Validate → Promote → Rollback 증적을 불변 digest로 연결합니다.</small>
          </div>
          <StatusBadge status={containerReadiness.data?.executor_supported ? 'healthy' : 'blocked'} />
        </header>
        <QueryState
          loading={containerReadiness.isLoading}
          error={containerReadiness.error}
          unavailable={containerReadiness.data?.available === false}
        />
        {containerReadiness.data ? (
          <>
            <div className="container-evidence-summary">
              <div>
                <span>개발 검증 대상</span>
                <strong>{containerReadiness.data.development_target.target}</strong>
                <code title={containerReadiness.data.development_target.target_identity || ''}>
                  {shortDigest(containerReadiness.data.development_target.target_identity)}
                </code>
              </div>
              <div>
                <span>운영 대상</span>
                <strong>{containerTarget?.name || '-'}</strong>
                <code title={containerTarget?.target_identity || ''}>
                  {shortDigest(containerTarget?.target_identity)}
                </code>
              </div>
              <div>
                <span>실행 지원</span>
                <strong>{containerReadiness.data.executor_supported ? '연결됨' : '미연결 · 조회 전용'}</strong>
                <small>{containerReadiness.data.agent?.hostname || containerReadiness.data.pipeline_state}</small>
              </div>
              <div>
                <span>원격 claim fence</span>
                <strong>{containerReadiness.data.remote_claim_fencing_ready ? 'epoch 고정됨' : '차단됨'}</strong>
                <small>DB lease · controller exclusive lock</small>
              </div>
            </div>

            <div className="container-pipeline-grid" aria-label="Docker deployment pipeline">
              <article>
                <header>
                  <span>1</span>
                  <h3>Build</h3>
                  <StatusBadge status={containerReadiness.data.latest_release ? 'success' : 'blocked'} />
                </header>
                <dl>
                  <div><dt>Release</dt><dd title={containerReadiness.data.latest_release?.release_digest}>{shortDigest(containerReadiness.data.latest_release?.release_digest)}</dd></div>
                  <div><dt>Source tree</dt><dd>{shortDigest(containerReadiness.data.latest_release?.source_tree)}</dd></div>
                  <div><dt>API image</dt><dd title={containerReadiness.data.latest_release?.api_image_digest}>{shortDigest(containerReadiness.data.latest_release?.api_image_digest, 19)}</dd></div>
                  <div><dt>Frontend image</dt><dd title={containerReadiness.data.latest_release?.frontend_image_digest}>{shortDigest(containerReadiness.data.latest_release?.frontend_image_digest, 19)}</dd></div>
                  <div><dt>Bundle SHA</dt><dd title={containerReadiness.data.latest_release?.bundle_sha256}>{shortDigest(containerReadiness.data.latest_release?.bundle_sha256)}</dd></div>
                </dl>
                <button className="button" type="button" disabled title="Docker build worker가 아직 연결되지 않았습니다.">Build 요청</button>
              </article>

              <article>
                <header>
                  <span>2</span>
                  <h3>Validate · an2p-dev</h3>
                  <StatusBadge status={containerReadiness.data.validation_receipt?.status || 'blocked'} />
                </header>
                <dl>
                  <div><dt>Receipt</dt><dd title={containerReadiness.data.validation_receipt?.receipt_digest}>{shortDigest(containerReadiness.data.validation_receipt?.receipt_digest)}</dd></div>
                  <div><dt>Result</dt><dd>{containerReadiness.data.validation_receipt?.status === 'passed' ? 'PASS' : containerReadiness.data.validation_receipt?.status === 'failed' ? 'FAIL' : '증적 없음'}</dd></div>
                  <div><dt>Target identity</dt><dd title={containerReadiness.data.validation_receipt?.target_identity}>{shortDigest(containerReadiness.data.validation_receipt?.target_identity)}</dd></div>
                  <div><dt>Validated</dt><dd>{formatDate(containerReadiness.data.validation_receipt?.validated_at)}</dd></div>
                  <div><dt>Expires</dt><dd>{formatDate(containerReadiness.data.validation_receipt?.expires_at)}</dd></div>
                </dl>
                <button className="button" type="button" disabled title="Docker validation worker가 아직 연결되지 않았습니다.">Validate 요청</button>
              </article>

              <article>
                <header>
                  <span>3</span>
                  <h3>Promote</h3>
                  <StatusBadge status={containerReadiness.data.promotion_evidence_ready ? 'ready' : 'blocked'} />
                </header>
                <dl>
                  <div><dt>대상</dt><dd>{containerTarget?.name || '-'}</dd></div>
                  <div><dt>Release</dt><dd>{shortDigest(containerReadiness.data.latest_release?.release_digest)}</dd></div>
                  <div><dt>Exact receipt</dt><dd>{shortDigest(containerReadiness.data.validation_receipt?.receipt_digest)}</dd></div>
                  <div><dt>Approval</dt><dd>{containerReadiness.data.promotion_approval ? shortDigest(containerReadiness.data.promotion_approval.id) : '없음 / 만료'}</dd></div>
                  <div><dt>Approval expiry</dt><dd>{formatDate(containerReadiness.data.promotion_approval?.expires_at)}</dd></div>
                  <div><dt>CAS generation</dt><dd>{containerReadiness.data.live_runtime_cas?.expected_runtime_generation ?? '-'}</dd></div>
                  <div><dt>State hash</dt><dd title={containerReadiness.data.live_runtime_cas?.expected_controller_state_sha256 || ''}>{shortDigest(containerReadiness.data.live_runtime_cas?.expected_controller_state_sha256)}</dd></div>
                </dl>
                {containerReadiness.data.actions.promote.required_confirmation ? (
                  <code className="container-confirmation" title={containerReadiness.data.actions.promote.required_confirmation}>
                    {containerReadiness.data.actions.promote.required_confirmation}
                  </code>
                ) : (
                  <p className="container-action-blocker">정확히 일치하는 유효한 an2p-dev PASS 영수증이 필요합니다.</p>
                )}
                <button
                  className="button primary"
                  type="button"
                  disabled={!canPromoteContainer}
                  onClick={() => {
                    setContainerAction('promote');
                    setContainerReason('');
                    setContainerConfirmation('');
                  }}
                  title={
                    !containerReadiness.data.promotion_evidence_ready
                      ? '유효한 exact PASS receipt가 없어 승격할 수 없습니다.'
                      : containerReadiness.data.executor_supported
                        ? 'Exact release와 PASS receipt로 운영 승격을 요청합니다.'
                        : 'an2p Docker deployment worker가 연결되지 않았습니다.'
                  }
                >
                  운영 승격
                </button>
              </article>

              <article>
                <header>
                  <span>4</span>
                  <h3>Rollback</h3>
                  <StatusBadge status={containerReadiness.data.rollback_evidence_ready ? 'ready' : 'blocked'} />
                </header>
                <dl>
                  <div><dt>Current</dt><dd title={containerState?.current_release_digest || ''}>{shortDigest(containerState?.current_release_digest)}</dd></div>
                  <div><dt>Previous</dt><dd title={containerState?.previous_release_digest || ''}>{shortDigest(containerState?.previous_release_digest)}</dd></div>
                  <div><dt>Last action</dt><dd>{containerState?.deployment_action || '-'}</dd></div>
                  <div><dt>Activated</dt><dd>{formatDate(containerState?.finished_at)}</dd></div>
                  <div><dt>CAS generation</dt><dd>{containerReadiness.data.live_runtime_cas?.expected_runtime_generation ?? '-'}</dd></div>
                  <div><dt>State hash</dt><dd title={containerReadiness.data.live_runtime_cas?.expected_controller_state_sha256 || ''}>{shortDigest(containerReadiness.data.live_runtime_cas?.expected_controller_state_sha256)}</dd></div>
                </dl>
                {containerReadiness.data.actions.rollback.required_confirmation ? (
                  <code className="container-confirmation" title={containerReadiness.data.actions.rollback.required_confirmation}>
                    {containerReadiness.data.actions.rollback.required_confirmation}
                  </code>
                ) : (
                  <p className="container-action-blocker">
                    현재 릴리스와 검증된 previous Docker digest가 모두 필요합니다.
                  </p>
                )}
                <button
                  className="button danger"
                  type="button"
                  disabled={!canRollbackContainer}
                  onClick={() => {
                    setContainerAction('rollback');
                    setContainerReason('');
                    setContainerConfirmation('');
                  }}
                  title={
                    canRollbackContainer
                      ? 'Exact current/previous digest로 롤백을 요청합니다.'
                      : 'Exact rollback evidence 또는 an2p worker가 준비되지 않았습니다.'
                  }
                >
                  이전 digest로 롤백
                </button>
              </article>

              <article>
                <header>
                  <span>5</span>
                  <h3>Native maintenance</h3>
                  <StatusBadge status={containerReadiness.data.native_rollback_evidence_ready ? 'ready' : 'blocked'} />
                </header>
                <dl>
                  <div><dt>Current Docker</dt><dd title={containerState?.current_release_digest || ''}>{shortDigest(containerState?.current_release_digest)}</dd></div>
                  <div><dt>Native baseline</dt><dd title={containerReadiness.data.live_runtime_cas?.native_baseline_identity || ''}>{shortDigest(containerReadiness.data.live_runtime_cas?.native_baseline_identity)}</dd></div>
                  <div><dt>Runtime</dt><dd>{containerState?.runtime_target_kind || 'native'}</dd></div>
                  <div><dt>CAS generation</dt><dd>{containerReadiness.data.live_runtime_cas?.expected_runtime_generation ?? '-'}</dd></div>
                  <div><dt>State hash</dt><dd title={containerReadiness.data.live_runtime_cas?.expected_controller_state_sha256 || ''}>{shortDigest(containerReadiness.data.live_runtime_cas?.expected_controller_state_sha256)}</dd></div>
                </dl>
                {containerReadiness.data.actions.rollback_native.required_confirmation ? (
                  <code className="container-confirmation" title={containerReadiness.data.actions.rollback_native.required_confirmation}>
                    {containerReadiness.data.actions.rollback_native.required_confirmation}
                  </code>
                ) : (
                  <p className="container-action-blocker">
                    활성 Docker 상태와 controller에 고정된 native baseline identity가 필요합니다.
                  </p>
                )}
                <button
                  className="button danger"
                  type="button"
                  disabled={!canRollbackNative}
                  onClick={() => {
                    setContainerAction('rollback_native');
                    setContainerReason('');
                    setContainerConfirmation('');
                  }}
                  title={
                    canRollbackNative
                      ? 'Exact live CAS와 native baseline으로 maintenance 전환을 요청합니다.'
                      : 'Native baseline 증적 또는 an2p deployment worker가 준비되지 않았습니다.'
                  }
                >
                  Native maintenance 전환
                </button>
              </article>
            </div>

            {containerReadiness.data.reasons.length ? (
              <div className="deploy-blockers" role="status" aria-label="Docker deployment blockers">
                {containerReadiness.data.reasons.map((reason) => (
                  <span key={reason.code}>{reason.message}</span>
                ))}
              </div>
            ) : null}
          </>
        ) : null}
      </section>

      <section className="panel">
        <header className="section-header">
          <div>
            <h2>Docker 승격·롤백 타임라인</h2>
            <small>릴리스, 검증 영수증, 현재/이전 digest가 연결된 작업만 표시합니다.</small>
          </div>
        </header>
        <QueryState
          loading={containerTimeline.isLoading}
          error={containerTimeline.error}
          unavailable={containerTimeline.data?.available === false}
          empty={containerTimeline.data?.items.length === 0}
        />
        {containerTimeline.data?.items.length ? (
          <ol className="container-timeline">
            {containerTimeline.data.items.map((item) => (
              <li key={item.id}>
                <div>
                  <strong>{item.deployment_action === 'rollback_native' ? 'Native maintenance' : item.deployment_action === 'rollback' ? 'Rollback' : 'Promote'} · {item.target}</strong>
                  <StatusBadge status={item.deployment_status} />
                </div>
                <p>
                  {item.runtime_target_kind === 'native' ? 'Native baseline' : 'Release'} <code title={item.runtime_target_kind === 'native' ? item.runtime_native_baseline_identity || '' : item.container_release_digest}>{shortDigest(item.runtime_target_kind === 'native' ? item.runtime_native_baseline_identity : item.container_release_digest)}</code>
                  {item.previous_container_release_digest ? (
                    <> · Previous <code title={item.previous_container_release_digest}>{shortDigest(item.previous_container_release_digest)}</code></>
                  ) : null}
                </p>
                <small>
                  Receipt {shortDigest(item.validation_receipt_digest)} · {item.validation_status === 'passed' ? 'PASS' : item.validation_status || '-'} · Runtime {item.runtime_target_kind || '-'} · {formatDate(item.created_at)}
                </small>
                {item.controller_state_sha256 ? (
                  <small>
                    Runtime #{item.runtime_generation || '-'} · Active {shortDigest(item.activated_release_digest)} · Previous {shortDigest(item.runtime_previous_release_digest)} · State {shortDigest(item.controller_state_sha256)}
                  </small>
                ) : null}
              </li>
            ))}
          </ol>
        ) : null}
      </section>

      <section className="panel">
        <header className="section-header">
          <div>
            <h2>네이티브 개발 스냅샷 (레거시)</h2>
            <small>기존 배포 endpoint 호환용입니다. Docker 승격 파이프라인과 혼용하지 않습니다.</small>
          </div>
        </header>
        <QueryState
          loading={readiness.isLoading}
          error={readiness.error}
          unavailable={readiness.data?.available === false && readinessReasons.length === 0}
        />
        {readiness.data && !snapshot && readinessReasons.length > 0 ? (
          <div className="deploy-blockers" role="status" aria-label="Deployment blockers">
            {readinessReasons.map((reason) => (
              <span key={reason.code}>{reason.message}</span>
            ))}
          </div>
        ) : null}
        {readiness.data && snapshot ? (
          <>
            <div className="deploy-source-grid">
              <div>
                <GitBranch size={18} aria-hidden="true" />
                <span>브랜치</span>
                <strong>{snapshot.branch}</strong>
              </div>
              <div>
                <ShieldCheck size={18} aria-hidden="true" />
                <span>Base HEAD</span>
                <strong className="mono-value">{snapshot.short_commit}</strong>
              </div>
              <div>
                <ShieldCheck size={18} aria-hidden="true" />
                <span>Source tree</span>
                <strong className="mono-value">{snapshot.short_source_tree}</strong>
              </div>
              <div>
                <Server size={18} aria-hidden="true" />
                <span>배포 Agent</span>
                <strong>{readiness.data.agent?.hostname || '연결 안 됨'}</strong>
              </div>
              <div>
                <CloudUpload size={18} aria-hidden="true" />
                <span>작업 트리</span>
                <strong>{snapshot.clean ? 'HEAD와 동일' : `변경 ${snapshot.changed_count}개 포함`}</strong>
              </div>
              <div>
                <Server size={18} aria-hidden="true" />
                <span>배포 경로</span>
                <strong>{snapshot.deploy_path_count}개 · 로컬 제외 {snapshot.excluded_count}개</strong>
              </div>
            </div>
            {readiness.data.reasons.length ? (
              <div className="deploy-blockers" role="status">
                {readiness.data.reasons.map((reason) => (
                  <span key={reason.code}>{reason.message}</span>
                ))}
              </div>
            ) : (
              <div className="deploy-ready" role="status">
                배포 조건이 충족되었습니다.
              </div>
            )}
          </>
        ) : null}
      </section>

      <section className="panel">
        <header className="section-header">
          <h2>배포 대상</h2>
        </header>
        <div className="deploy-target-grid">
          {readiness.data?.targets.map((target) => (
            <article key={target.name}>
              <div>
                <Server size={18} aria-hidden="true" />
                <strong>{target.name}</strong>
                <StatusBadge status={target.active ? 'healthy' : 'idle'} />
              </div>
              <dl>
                <div>
                  <dt>역할</dt>
                  <dd>{target.role}</dd>
                </div>
                <div>
                  <dt>호스트</dt>
                  <dd>{target.server}</dd>
                </div>
                <div>
                  <dt>운영 서비스</dt>
                  <dd>{targetServiceSummary(target)}</dd>
                </div>
                <div>
                  <dt>도메인</dt>
                  <dd>{target.domain}</dd>
                </div>
                <div>
                  <dt>배포 키</dt>
                  <dd>{target.key_ready ? '준비됨' : '확인 필요'}</dd>
                </div>
              </dl>
            </article>
          ))}
        </div>
      </section>

      {readiness.data?.topology?.crawler_workers?.length ? (
        <section className="panel">
          <header className="section-header">
            <div>
              <h2>분산 크롤러 워커</h2>
              <small>
                인벤토리 검토만 완료되었습니다. 안전한 bootstrap과 원자적 계정 등록이 없어 설치는 차단됩니다.
              </small>
            </div>
          </header>
          <div className="deploy-target-grid">
            {readiness.data.topology.crawler_workers.map((worker) => (
              <article key={worker.worker_key}>
                <div>
                  <Server size={18} aria-hidden="true" />
                  <strong>{worker.worker_key}</strong>
                  <StatusBadge status="idle" />
                </div>
                <dl>
                  <div>
                    <dt>토폴로지 노드 / DNS</dt>
                    <dd>{worker.topology_node} · {worker.dns_host}</dd>
                  </div>
                  <div>
                    <dt>커널 호스트명</dt>
                    <dd>{worker.kernel_hostname}</dd>
                  </div>
                  <div>
                    <dt>배포 순서</dt>
                    <dd>#{worker.rollout_order}{worker.canary ? ' · canary' : ''}</dd>
                  </div>
                  <div>
                    <dt>희망 상태</dt>
                    <dd>{worker.enabled ? '활성화 승인됨 · 관측 대기' : '비활성 · 인벤토리 준비'}</dd>
                  </div>
                  <div>
                    <dt>설치 상태</dt>
                    <dd>차단 · NOT READY</dd>
                  </div>
                  <div>
                    <dt>관측 상태</dt>
                    <dd>관측 정보 없음</dd>
                  </div>
                  <div>
                    <dt>자원 제한</dt>
                    <dd>
                      concurrency {worker.concurrency} · high {worker.memory_high} · max {worker.memory_max} · CPU {worker.cpu_quota}
                    </dd>
                  </div>
                </dl>
              </article>
            ))}
          </div>
        </section>
      ) : null}

      <section className="panel">
        <header className="section-header">
          <h2>현재 실행 버전</h2>
        </header>
        <QueryState loading={current.isLoading} error={current.error} unavailable={current.data?.available === false} empty={current.data?.items.length === 0} />
        {current.data?.items.length ? (
          <DataTable data={current.data.items} columns={currentColumns} exportName="mooncen-current-services.csv" />
        ) : null}
      </section>

      <section className="panel">
        <header className="section-header">
          <h2>배포 작업 이력</h2>
        </header>
        <QueryState loading={deployments.isLoading} error={deployments.error} unavailable={deployments.data?.available === false} empty={deployments.data?.items.length === 0} />
        {deployments.data?.items.length ? (
          <DataTable
            data={deployments.data.items}
            columns={columns}
            exportName="mooncen-deployments.csv"
            onRowClick={(row) => navigate(`/deployments/${row.id}`)}
          />
        ) : null}
      </section>

      {showDeploy && (
        <DetailPanel title="네이티브 개발 스냅샷 배포 (레거시)" onClose={() => setShowDeploy(false)}>
          <form
            className="stack-form"
            onSubmit={(event) => {
              event.preventDefault();
              if (confirmation === confirmationText) createDeployment.mutate();
            }}
          >
            <label>
              배포 대상
              <select
                value={selectedTarget}
                onChange={(event) => {
                  setSelectedTarget(event.target.value);
                  setConfirmation('');
                }}
              >
                {readiness.data?.targets.map((target) => (
                  <option key={target.name} value={target.name} disabled={!target.key_ready}>
                    {target.name} · {target.role} · {target.server}
                  </option>
                ))}
              </select>
            </label>
            <label>
              개발 스냅샷
              <input className="mono-value" value={snapshot?.source_tree || ''} readOnly />
            </label>
            <label className="check-row">
              <input type="checkbox" checked={skipWorkers} onChange={(event) => setSkipWorkers(event.target.checked)} />
              크롤러·AI worker 갱신 제외
            </label>
            <label>
              확인 문구
              <code className="confirmation-code">{confirmationText}</code>
              <input
                aria-label="확인 문구"
                value={confirmation}
                onChange={(event) => setConfirmation(event.target.value)}
                autoComplete="off"
                spellCheck={false}
                required
              />
            </label>
            <QueryState error={createDeployment.error} />
            <button
              className="button primary button-with-icon"
              type="submit"
              disabled={!canCreate || confirmation !== confirmationText || createDeployment.isPending}
            >
              <CloudUpload size={17} aria-hidden="true" />
              {createDeployment.isPending ? '등록 중…' : `${selectedTarget} 배포 시작`}
            </button>
          </form>
        </DetailPanel>
      )}

      {containerAction && containerReadiness.data && containerTarget && (
        <DetailPanel
          title={containerAction === 'promote' ? 'Docker 운영 승격' : containerAction === 'rollback' ? 'Docker previous digest 롤백' : 'Docker → Native maintenance'}
          onClose={() => {
            if (!requestContainerAction.isPending) {
              setContainerAction(null);
              setContainerReason('');
              setContainerConfirmation('');
            }
          }}
        >
          <form
            className="stack-form"
            onSubmit={(event) => {
              event.preventDefault();
              const required = containerReadiness.data?.actions[containerAction].required_confirmation;
              if (containerReason.trim().length >= 3 && containerConfirmation === required) {
                requestContainerAction.mutate();
              }
            }}
          >
            <label>
              고정 운영 대상
              <input value={`${containerTarget.name} · production · ${containerTarget.target_identity}`} readOnly />
            </label>
            <label>
              {containerAction === 'promote' ? '승격 release / PASS receipt' : containerAction === 'rollback' ? '현재 release / rollback release' : '현재 Docker / native baseline'}
              <input
                className="mono-value"
                value={containerAction === 'promote'
                  ? `${containerReadiness.data.latest_release?.release_digest || ''} / ${containerReadiness.data.validation_receipt?.receipt_digest || ''}`
                  : containerAction === 'rollback'
                    ? `${containerState?.current_release_digest || ''} / ${containerState?.previous_release_digest || ''}`
                    : `${containerState?.current_release_digest || ''} / ${containerReadiness.data.live_runtime_cas?.native_baseline_identity || ''}`}
                readOnly
              />
            </label>
            <label>
              변경 사유
              <textarea
                aria-label="Docker 배포 사유"
                value={containerReason}
                onChange={(event) => setContainerReason(event.target.value)}
                maxLength={500}
                required
              />
            </label>
            <label>
              확인 문구
              <code className="confirmation-code">
                {containerReadiness.data.actions[containerAction].required_confirmation}
              </code>
              <input
                aria-label="Docker 확인 문구"
                value={containerConfirmation}
                onChange={(event) => setContainerConfirmation(event.target.value)}
                autoComplete="off"
                spellCheck={false}
                required
              />
            </label>
            <p className="container-action-blocker">
              원격 controller 시작 후에는 취소할 수 없습니다. 결과는 cloud의 transaction 종료와 exact runtime state를 재조회해 확정합니다.
            </p>
            <QueryState error={requestContainerAction.error} />
            <button
              className={`button ${containerAction === 'promote' ? 'primary' : 'danger'}`}
              type="submit"
              disabled={
                requestContainerAction.isPending ||
                containerReason.trim().length < 3 ||
                containerConfirmation !== containerReadiness.data.actions[containerAction].required_confirmation
              }
            >
              {requestContainerAction.isPending
                ? 'Queue 등록 중…'
                : containerAction === 'promote'
                  ? 'Docker 운영 승격 시작'
                  : containerAction === 'rollback'
                    ? 'Docker 롤백 시작'
                    : 'Native maintenance 시작'}
            </button>
          </form>
        </DetailPanel>
      )}

      {id && (
        <DetailPanel title="배포 상세" onClose={() => navigate('/deployments')}>
          <QueryState loading={detail.isLoading} error={detail.error || cancelDeployment.error} />
          {detail.data ? (
            <>
              <DefinitionList value={detail.data} />
              {session.role === 'admin' && ['queued', 'assigned', 'running'].includes(String(detail.data.job_status)) && !(
                detail.data.deployment_mode === 'container' && ['assigned', 'running'].includes(String(detail.data.job_status))
              ) ? (
                <button
                  className="button danger"
                  type="button"
                  disabled={cancelDeployment.isPending}
                  onClick={() => {
                    const confirmed = window.prompt(`배포 취소 확인을 위해 CANCEL ${String(detail.data?.target || '')}을 입력하세요.`);
                    if (confirmed === `CANCEL ${String(detail.data?.target || '')}`) cancelDeployment.mutate();
                  }}
                >
                  배포 취소 요청
                </button>
              ) : null}
              {detail.data.deployment_mode === 'container' && ['assigned', 'running'].includes(String(detail.data.job_status)) ? (
                <p className="container-action-blocker" role="status">
                  원격 Docker controller가 시작된 뒤에는 취소할 수 없습니다. worker가 cloud 상태를 재조정하고 있습니다.
                </p>
              ) : null}
            </>
          ) : null}
          <h3>실시간 배포 로그</h3>
          <StreamStatus state={stream.state} detail={stream.detail} />
          <QueryState loading={logs.isLoading} error={logs.error} empty={logs.data?.items.length === 0} />
          {logs.data?.items.length ? (
            <div className="log-viewer">
              {logs.data.items.map((log) => (
                <div key={String(log.id)}>
                  <time>{formatDate(log.created_at)}</time>
                  <strong>{String(log.log_level || 'info')}</strong>
                  <span>{String(log.message || '')}</span>
                </div>
              ))}
            </div>
          ) : null}
        </DetailPanel>
      )}
    </>
  );
}
