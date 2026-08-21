import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { opsApi } from '../api';
import DataTable from '../components/DataTable';
import StatusBadge from '../components/StatusBadge';
import { DefinitionList, DetailPanel, PageHeader, QueryState, StatCard } from '../components/Ui';
import { useOpsSession } from '../context';
import type { PageResponse } from '../types';
import { formatDate, formatNumber } from '../utils';

type Artifact = Record<string, unknown> & {
  artifact_digest: string;
  code_version: string;
  config_revision: string;
  size_bytes: number;
  key_id: string;
  created_at: string;
};
type Rollout = Record<string, unknown> & {
  id: string;
  rollout_epoch: number;
  artifact_digest: string;
  previous_artifact_digest?: string | null;
  status: string;
  strategy: string;
  requested_worker_count: number;
  created_at: string;
};
type Worker = Record<string, unknown> & {
  worker_key: string;
  hostname?: string | null;
  agent_status?: string | null;
  maintenance_mode?: boolean;
  rollout_id?: string | null;
  generation: number;
  desired_status: string;
  cohort: string;
  artifact_digest?: string | null;
  code_version?: string | null;
  config_revision?: string | null;
  report_status?: string | null;
  reported_rollout_id?: string | null;
  reported_generation?: number | null;
  reported_artifact_digest?: string | null;
  reported_code_version?: string | null;
  reported_config_revision?: string | null;
  release_converged?: boolean;
  historical_identity_matched?: boolean;
  is_current_desired?: boolean;
  error_code?: string | null;
  reported_at?: string | null;
};
type ReleaseAction = Record<string, unknown> & {
  id: string;
  action: string;
  status: string;
  expected_generation: number;
  requested_by: string;
  reason: string;
  request_digest?: string | null;
  approval_status?: 'pending' | 'approved' | 'expired' | null;
  approval_database_login?: string | null;
  approval_operator_label?: string | null;
  approved_at?: string | null;
  approval_expires_at?: string | null;
  error_code?: string | null;
  created_at: string;
};
type ReleaseSummary = {
  available: boolean;
  environment?: string;
  artifact_count?: number;
  worker_count?: number;
  unhealthy_worker_count?: number;
  pending_action_count?: number;
  active_rollout?: Rollout | null;
  action_capabilities?: Partial<Record<ActionKind, { available: boolean; reason?: string | null }>>;
};
type ActionKind = 'build' | 'register_artifact' | 'create_canary' | 'advance_rollout' | 'pause_rollout' | 'rollback_rollout' | 'complete_rollback';
type RolloutPhase = 'rolling' | 'complete';

function shortDigest(value: unknown): string {
  const digest = String(value || '');
  return digest ? `${digest.slice(0, 12)}…` : '-';
}

function newIdempotencyKey(): string {
  return `crawler.release:${crypto.randomUUID()}`;
}

function workerList(value: string): string[] {
  return [...new Set(value.split(/[\s,]+/).map((item) => item.trim()).filter(Boolean))];
}

export async function crawlerReleaseWorkerSetDigest(workerKeys: string[]): Promise<string> {
  const canonical = [...workerKeys].sort().join('\n');
  const bytes = new TextEncoder().encode(canonical);
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, '0')).join('').slice(0, 12);
}

function actionLabel(value: ActionKind): string {
  return {
    build: '빌드 요청',
    register_artifact: '아티팩트 등록',
    create_canary: 'Canary 생성',
    advance_rollout: 'Rollout 진행',
    pause_rollout: 'Rollout 일시정지',
    rollback_rollout: 'Rollout 롤백',
    complete_rollback: 'Rollback 완료 확인',
  }[value];
}

export default function CrawlerReleasesPage() {
  const session = useOpsSession();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { rolloutId, actionId } = useParams();
  const [showActionForm, setShowActionForm] = useState(false);
  const [action, setAction] = useState<ActionKind>('create_canary');
  const [idempotencyKey, setIdempotencyKey] = useState(newIdempotencyKey);
  const [expectedGeneration, setExpectedGeneration] = useState(0);
  const [reason, setReason] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [sourceCommit, setSourceCommit] = useState('');
  const [sourceTree, setSourceTree] = useState('');
  const [testProfile, setTestProfile] = useState<'crawler' | 'crawler_full'>('crawler');
  const [buildRequestId, setBuildRequestId] = useState('');
  const [artifactDigest, setArtifactDigest] = useState('');
  const [baselineDigest, setBaselineDigest] = useState('');
  const [codeVersion, setCodeVersion] = useState('');
  const [configRevision, setConfigRevision] = useState('');
  const [formRolloutId, setFormRolloutId] = useState('');
  const [rolloutPhase, setRolloutPhase] = useState<RolloutPhase>('rolling');
  const [workersText, setWorkersText] = useState('');
  const [targetWorkersText, setTargetWorkersText] = useState('');
  const [workerSetDigest, setWorkerSetDigest] = useState('');

  const summary = useQuery({
    queryKey: ['crawler-release-summary', session.environment],
    queryFn: () => opsApi<ReleaseSummary>('/crawler-control/summary'),
    refetchInterval: 15_000,
  });
  const artifacts = useQuery({
    queryKey: ['crawler-release-artifacts', session.environment],
    queryFn: () => opsApi<PageResponse<Artifact>>('/crawler-control/artifacts?limit=100&offset=0'),
    refetchInterval: 30_000,
  });
  const rollouts = useQuery({
    queryKey: ['crawler-release-rollouts', session.environment],
    queryFn: () => opsApi<PageResponse<Rollout>>('/crawler-control/rollouts?limit=100&offset=0'),
    refetchInterval: 15_000,
  });
  const workers = useQuery({
    queryKey: ['crawler-release-workers', session.environment],
    queryFn: () => opsApi<{ available: boolean; items: Worker[] }>('/crawler-control/workers'),
    refetchInterval: 15_000,
  });
  const actions = useQuery({
    queryKey: ['crawler-release-actions', session.environment],
    queryFn: () => opsApi<PageResponse<ReleaseAction>>('/crawler-control/actions?limit=100&offset=0'),
    refetchInterval: 10_000,
  });
  const rolloutDetail = useQuery({
    queryKey: ['crawler-release-rollout', session.environment, rolloutId],
    queryFn: () => opsApi<{
      available: boolean;
      item: Rollout | null;
      workers: Worker[];
      worker_history_available: boolean | null;
      worker_history_reason: string | null;
    }>(`/crawler-control/rollouts/${rolloutId}`),
    enabled: Boolean(rolloutId),
    refetchInterval: rolloutId ? 10_000 : false,
  });
  const actionDetail = useQuery({
    queryKey: ['crawler-release-action', session.environment, actionId],
    queryFn: () => opsApi<{ available: boolean; item: ReleaseAction | null }>(`/crawler-control/actions/${actionId}`),
    enabled: Boolean(actionId),
    refetchInterval: actionId ? 10_000 : false,
  });

  const relevantWorkerKeys = useMemo(
    () => action === 'create_canary'
      ? workerList(workersText)
      : action === 'advance_rollout' && rolloutPhase === 'rolling'
        ? workerList(targetWorkersText)
        : [],
    [action, rolloutPhase, targetWorkersText, workersText],
  );
  useEffect(() => {
    let current = true;
    if (!relevantWorkerKeys.length) {
      setWorkerSetDigest('');
      return () => { current = false; };
    }
    void crawlerReleaseWorkerSetDigest(relevantWorkerKeys).then((value) => {
      if (current) setWorkerSetDigest(value);
    });
    return () => { current = false; };
  }, [relevantWorkerKeys]);

  const requiredConfirmation = useMemo(() => {
    if (action === 'build') return `BUILD ${sourceTree.trim().slice(0, 12)}`;
    if (action === 'register_artifact') return `REGISTER ${artifactDigest.trim().slice(0, 12)}`;
    if (action === 'create_canary') return workerSetDigest
      ? `CANARY ${formRolloutId.trim()} ${expectedGeneration} ${artifactDigest.trim().slice(0, 12)} ${baselineDigest.trim().slice(0, 12)} ${workerSetDigest}`
      : '';
    if (action === 'advance_rollout') return rolloutPhase === 'complete'
      ? `ADVANCE ${formRolloutId.trim()} ${expectedGeneration} complete none`
      : workerSetDigest
        ? `ADVANCE ${formRolloutId.trim()} ${expectedGeneration} rolling ${workerSetDigest}`
        : '';
    if (action === 'pause_rollout') return `PAUSE ${formRolloutId.trim()} ${expectedGeneration}`;
    if (action === 'rollback_rollout') return `ROLLBACK ${formRolloutId.trim()} ${expectedGeneration}`;
    return `COMPLETE_ROLLBACK ${formRolloutId.trim()} ${expectedGeneration}`;
  }, [action, artifactDigest, baselineDigest, expectedGeneration, formRolloutId, rolloutPhase, sourceTree, workerSetDigest]);

  const actionMutation = useMutation<{ available: true; replayed: boolean; item: ReleaseAction }, Error, Record<string, unknown>>({
    mutationFn: (body) => opsApi('/crawler-control/actions', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: (result) => {
      setShowActionForm(false);
      setIdempotencyKey(newIdempotencyKey());
      setConfirmation('');
      setReason('');
      void queryClient.invalidateQueries({
        queryKey: ['crawler-release-summary', session.environment],
      });
      void queryClient.invalidateQueries({
        queryKey: ['crawler-release-actions', session.environment],
      });
      navigate(`/crawler-releases/actions/${result.item.id}`);
    },
  });

  const actionAvailable = (candidate: ActionKind): boolean => (
    summary.data?.available === true
    && summary.data.action_capabilities?.[candidate]?.available === true
  );

  const openAction = (nextAction: ActionKind, rollout?: Rollout) => {
    if (!actionAvailable(nextAction)) return;
    const latestRolloutEpoch = Math.max(0, ...(rollouts.data?.items || []).map((item) => Number(item.rollout_epoch || 0)));
    const currentRolloutEpoch = Number(rollout?.rollout_epoch || summary.data?.active_rollout?.rollout_epoch || latestRolloutEpoch);
    setAction(nextAction);
    setExpectedGeneration(nextAction === 'create_canary'
      ? latestRolloutEpoch + 1
      : ['advance_rollout', 'pause_rollout', 'rollback_rollout', 'complete_rollback'].includes(nextAction)
        ? Math.max(1, currentRolloutEpoch)
        : 0);
    setFormRolloutId(nextAction === 'create_canary'
      ? crypto.randomUUID()
      : rollout?.id || summary.data?.active_rollout?.id || '');
    if (nextAction === 'create_canary') {
      const desiredDigests = [...new Set(
        (workers.data?.items || [])
          .map((item) => String(item.artifact_digest || '').trim())
          .filter(Boolean),
      )];
      const activeRollout = summary.data?.active_rollout;
      const baseline = desiredDigests.length === 1
        ? desiredDigests[0]
        : activeRollout?.status === 'rolled_back'
          ? String(activeRollout.previous_artifact_digest || '')
          : '';
      const target = artifacts.data?.items.find((item) => item.artifact_digest !== baseline)?.artifact_digest || '';
      setArtifactDigest(target);
      setBaselineDigest(baseline);
    }
    setIdempotencyKey(newIdempotencyKey());
    setConfirmation('');
    setShowActionForm(true);
  };

  const submitAction = (event: FormEvent) => {
    event.preventDefault();
    const body: Record<string, unknown> = {
      action,
      idempotency_key: idempotencyKey,
      environment: session.environment,
      expected_generation: expectedGeneration,
      confirmation,
      reason,
    };
    if (action === 'build') Object.assign(body, { source_commit: sourceCommit, source_tree: sourceTree, test_profile: testProfile });
    if (action === 'register_artifact') Object.assign(body, { build_request_id: buildRequestId, artifact_digest: artifactDigest, code_version: codeVersion, config_revision: configRevision });
    if (action === 'create_canary') Object.assign(body, { artifact_digest: artifactDigest, baseline_digest: baselineDigest, rollout_id: formRolloutId, worker_keys: workerList(workersText) });
    if (action === 'advance_rollout') Object.assign(body, { rollout_id: formRolloutId, rollout_phase: rolloutPhase, target_worker_keys: rolloutPhase === 'rolling' ? workerList(targetWorkersText) : [] });
    if (['pause_rollout', 'rollback_rollout', 'complete_rollback'].includes(action)) Object.assign(body, { rollout_id: formRolloutId });
    actionMutation.mutate(body);
  };

  const artifactColumns = useMemo<ColumnDef<Artifact>[]>(() => [
    { accessorKey: 'artifact_digest', header: 'Digest', cell: ({ row }) => <code title={row.original.artifact_digest}>{shortDigest(row.original.artifact_digest)}</code> },
    { accessorKey: 'code_version', header: '코드 버전' },
    { accessorKey: 'config_revision', header: '설정 리비전' },
    { accessorKey: 'size_bytes', header: '크기', cell: ({ row }) => formatNumber(row.original.size_bytes) },
    { accessorKey: 'key_id', header: '서명 키 ID' },
    { accessorKey: 'created_at', header: '등록', cell: ({ row }) => formatDate(row.original.created_at) },
  ], []);
  const rolloutColumns = useMemo<ColumnDef<Rollout>[]>(() => [
    { accessorKey: 'rollout_epoch', header: '세대' },
    { accessorKey: 'status', header: '상태', cell: ({ row }) => <StatusBadge status={row.original.status} /> },
    { accessorKey: 'strategy', header: '전략', cell: ({ row }) => typeof row.original.strategy === 'string' ? row.original.strategy : JSON.stringify(row.original.strategy) },
    { accessorKey: 'artifact_digest', header: '대상 Digest', cell: ({ row }) => <code>{shortDigest(row.original.artifact_digest)}</code> },
    { accessorKey: 'requested_worker_count', header: 'Worker', cell: ({ row }) => formatNumber(row.original.requested_worker_count) },
    { accessorKey: 'created_at', header: '생성', cell: ({ row }) => formatDate(row.original.created_at) },
  ], []);
  const workerColumns = useMemo<ColumnDef<Worker>[]>(() => [
    { accessorKey: 'worker_key', header: 'Worker' },
    { accessorKey: 'hostname', header: 'Host' },
    { accessorKey: 'cohort', header: 'Cohort' },
    { accessorKey: 'generation', header: '목표 세대' },
    { accessorKey: 'desired_status', header: '목표', cell: ({ row }) => <StatusBadge status={row.original.desired_status} /> },
    { accessorKey: 'report_status', header: '보고', cell: ({ row }) => <StatusBadge status={row.original.report_status} /> },
    {
      id: 'release_match',
      header: '일치',
      cell: ({ row }) => row.original.release_converged === true
        ? '현재 일치'
        : row.original.historical_identity_matched === true
          ? '이력 일치'
          : '불일치',
    },
    { accessorKey: 'reported_at', header: '최근 보고', cell: ({ row }) => formatDate(row.original.reported_at) },
  ], []);
  const actionColumns = useMemo<ColumnDef<ReleaseAction>[]>(() => [
    { accessorKey: 'action', header: '요청' },
    { accessorKey: 'status', header: '상태', cell: ({ row }) => <StatusBadge status={row.original.status} /> },
    {
      accessorKey: 'approval_status',
      header: '독립 승인',
      cell: ({ row }) => <StatusBadge status={row.original.approval_status || 'pending'} />,
    },
    { accessorKey: 'expected_generation', header: '기대 세대' },
    { accessorKey: 'request_digest', header: '요청 해시', cell: ({ row }) => <code title={String(row.original.request_digest || '')}>{shortDigest(row.original.request_digest)}</code> },
    { accessorKey: 'reason', header: '사유' },
    { accessorKey: 'error_code', header: '오류', cell: ({ row }) => row.original.error_code || '-' },
    { accessorKey: 'created_at', header: '요청 시각', cell: ({ row }) => formatDate(row.original.created_at) },
  ], []);
  const controlUnavailable = summary.data?.available === false;
  const liveRolloutExists = ['planned', 'running', 'paused', 'rolling_back'].includes(String(summary.data?.active_rollout?.status || ''));
  const builderUnavailable = summary.data?.action_capabilities?.build?.available !== true
    || summary.data?.action_capabilities?.register_artifact?.available !== true;
  const releaseMutationsUnavailable = (['create_canary', 'advance_rollout', 'pause_rollout', 'rollback_rollout', 'complete_rollback'] as ActionKind[])
    .every((candidate) => summary.data?.action_capabilities?.[candidate]?.available !== true);

  return (
    <>
      <PageHeader
        eyebrow="CENTRAL RELEASE CONTROL"
        title="Crawler Releases"
        description="중앙 서버에 등록된 서명 아티팩트, 단계적 배포, Worker 수렴 상태와 감사 가능한 변경 요청을 관리합니다. 요청은 대기열에만 기록되며 브라우저에서 배포를 직접 실행하지 않습니다."
        actions={(
          <>
            <Link className="button subtle" to="/crawler-studio">Studio</Link>
            <Link className="button subtle" to="/crawler-analytics">운영 분석</Link>
            {session.role === 'admin' && <button className="button primary" type="button" disabled>{releaseMutationsUnavailable ? '배포 변경 잠김' : '별도 승인 필요'}</button>}
          </>
        )}
      />
      <QueryState loading={summary.isLoading} error={summary.error} />
      {controlUnavailable && (
        <div className="deploy-blockers crawler-control-unavailable" role="alert">
          <strong>중앙 크롤러 제어 DB에 연결되지 않았습니다.</strong>
          <span>릴리스 수치가 0이라는 뜻이 아닙니다. 전용 연결과 DB marker를 확인해야 조회와 변경 요청을 사용할 수 있습니다.</span>
        </div>
      )}
      {summary.data?.available && builderUnavailable && (
        <div className="deploy-blockers crawler-builder-unavailable" role="status">
          <strong>빌드·아티팩트 등록 게이트가 닫혀 있습니다.</strong>
          <span>격리 빌더의 불변 증거와 signer 인계가 아직 연결되지 않았습니다. Build와 아티팩트 등록은 차단되며 기존 릴리스 조회·분석에는 영향을 주지 않습니다.</span>
        </div>
      )}
      {summary.data?.available && releaseMutationsUnavailable && (
        <div className="deploy-blockers crawler-release-approval-unavailable" role="status">
          <strong>Rollout 변경 승인 경계가 준비되지 않았습니다.</strong>
          <span>독립 approver 또는 중앙 action consumer의 신선한 heartbeat를 확인할 수 없어 Canary·진행·일시정지·롤백 요청을 차단합니다.</span>
        </div>
      )}
      {summary.data?.available && !releaseMutationsUnavailable && (
        <div className="deploy-blockers crawler-release-approval-ready" role="status">
          <strong>변경 요청에는 별도 운영자 승인이 필요합니다.</strong>
          <span>Ops Console은 요청과 SHA-256만 대기열에 기록합니다. 분리된 approver가 정확한 요청을 확인해 짧은 유효시간의 영수증을 발행하기 전에는 중앙 서버가 실행하지 않습니다.</span>
        </div>
      )}
      {summary.data?.available ? (
        <section className="stats-grid">
          <StatCard label="등록 아티팩트" value={formatNumber(summary.data.artifact_count)} />
          <StatCard label="관리 Worker" value={formatNumber(summary.data.worker_count)} />
          <StatCard label="비정상 Worker" value={formatNumber(summary.data.unhealthy_worker_count)} tone={summary.data.unhealthy_worker_count ? 'bad' : 'good'} />
          <StatCard label="대기 요청" value={formatNumber(summary.data.pending_action_count)} tone={summary.data.pending_action_count ? 'warn' : 'neutral'} />
          <StatCard label="최근 Rollout" value={summary.data.active_rollout?.rollout_epoch ?? '-'} note={summary.data.active_rollout?.status || '기록 없음'} />
          <StatCard label="환경" value={summary.data.environment?.toUpperCase() || session.environment.toUpperCase()} />
        </section>
      ) : null}

      <section className="panel">
        <header className="section-header"><div><h2>서명 아티팩트</h2><small>빌드 산출물 자체나 비밀 키는 노출하지 않고 식별자와 서명 키 ID만 표시합니다.</small></div>{session.role === 'admin' && <button className="button subtle" type="button" disabled={!actionAvailable('register_artifact')} title={builderUnavailable ? '격리 빌더·서명 증거 인계가 연결된 뒤 사용할 수 있습니다.' : '아티팩트 등록 요청'} onClick={() => openAction('register_artifact')}>아티팩트 등록 요청</button>}</header>
        <QueryState loading={artifacts.isLoading} error={artifacts.error} unavailable={artifacts.data?.available === false} empty={artifacts.data?.available && artifacts.data.items.length === 0} />
        {artifacts.data?.available && artifacts.data.items.length ? <DataTable data={artifacts.data.items} columns={artifactColumns} exportName="crawler-release-artifacts.csv" /> : null}
      </section>

      <section className="panel">
        <header className="section-header"><div><h2>Rollout</h2><small>Canary, rolling, complete 단계와 이전 아티팩트를 추적합니다.</small></div>{session.role === 'admin' && <button className="button subtle" type="button" disabled={!actionAvailable('create_canary') || liveRolloutExists} title={liveRolloutExists ? '진행 중인 Rollout을 먼저 완료하거나 롤백하세요.' : '새 Canary 요청'} onClick={() => openAction('create_canary')}>Canary 요청</button>}</header>
        <QueryState loading={rollouts.isLoading} error={rollouts.error} unavailable={rollouts.data?.available === false} empty={rollouts.data?.available && rollouts.data.items.length === 0} />
        {rollouts.data?.available && rollouts.data.items.length ? <DataTable data={rollouts.data.items} columns={rolloutColumns} exportName="crawler-release-rollouts.csv" onRowClick={(row) => navigate(`/crawler-releases/rollouts/${row.id}`)} /> : null}
      </section>

      <section className="panel">
        <header className="section-header"><div><h2>Worker 수렴 상태</h2><small>중앙 desired state와 Worker가 보고한 Rollout·세대·아티팩트·코드·설정의 정확한 일치 여부입니다.</small></div></header>
        <QueryState loading={workers.isLoading} error={workers.error} unavailable={workers.data?.available === false} empty={workers.data?.available && workers.data.items.length === 0} />
        {workers.data?.available && workers.data.items.length ? <DataTable data={workers.data.items} columns={workerColumns} exportName="crawler-release-workers.csv" /> : null}
      </section>

      <section className="panel">
        <header className="section-header"><div><h2>변경 요청 · 감사 이력</h2><small>권한 있는 중앙 실행기가 처리할 대기열 상태와 결과입니다.</small></div></header>
        <QueryState loading={actions.isLoading} error={actions.error} unavailable={actions.data?.available === false} empty={actions.data?.available && actions.data.items.length === 0} />
        {actionMutation.error && <QueryState error={actionMutation.error} />}
        {actions.data?.available && actions.data.items.length ? <DataTable data={actions.data.items} columns={actionColumns} exportName="crawler-release-actions.csv" onRowClick={(row) => navigate(`/crawler-releases/actions/${row.id}`)} /> : null}
      </section>

      {rolloutId && (
        <DetailPanel title="Rollout 상세" onClose={() => navigate('/crawler-releases')}>
          <QueryState loading={rolloutDetail.isLoading} error={rolloutDetail.error} unavailable={rolloutDetail.data?.available === false} />
          {rolloutDetail.data?.item ? (
            <>
              {session.role === 'admin' && (
                <div className="button-row release-action-row">
                  {rolloutDetail.data.item.status === 'rolling_back' ? (
                    <button className="button primary" type="button" disabled={!actionAvailable('complete_rollback')} onClick={() => openAction('complete_rollback', rolloutDetail.data?.item || undefined)}>롤백 완료 확인</button>
                  ) : (
                    <>
                      <button className="button subtle" type="button" disabled={!actionAvailable('advance_rollout')} onClick={() => openAction('advance_rollout', rolloutDetail.data?.item || undefined)}>진행</button>
                      <button className="button subtle" type="button" disabled={!actionAvailable('pause_rollout')} onClick={() => openAction('pause_rollout', rolloutDetail.data?.item || undefined)}>일시정지</button>
                      <button className="button danger" type="button" disabled={!actionAvailable('rollback_rollout')} onClick={() => openAction('rollback_rollout', rolloutDetail.data?.item || undefined)}>롤백</button>
                    </>
                  )}
                </div>
              )}
              <DefinitionList value={rolloutDetail.data.item} />
              <h3>대상 Worker</h3>
              {rolloutDetail.data.worker_history_available === false ? (
                <div className="deploy-blockers" role="status">
                  <strong>이 Rollout의 Worker 이력 증거를 확인할 수 없습니다.</strong>
                  <span>Worker 이력 스냅샷 계약 도입 전에 생성된 Rollout입니다. 0명으로 해석하지 마세요.</span>
                </div>
              ) : (
                <>
                  <QueryState empty={rolloutDetail.data.workers.length === 0} />
                  {rolloutDetail.data.workers.length ? <DataTable data={rolloutDetail.data.workers} columns={workerColumns} exportName="crawler-rollout-workers.csv" /> : null}
                </>
              )}
            </>
          ) : null}
        </DetailPanel>
      )}
      {actionId && (
        <DetailPanel title="릴리스 요청 상세" onClose={() => navigate('/crawler-releases')}>
          <QueryState loading={actionDetail.isLoading} error={actionDetail.error} unavailable={actionDetail.data?.available === false} />
          {actionDetail.data?.item && <DefinitionList value={actionDetail.data.item} />}
        </DetailPanel>
      )}
      {showActionForm && (
        <DetailPanel title={actionLabel(action)} onClose={() => setShowActionForm(false)}>
          <form className="stack-form" onSubmit={submitAction}>
            <label>
              작업
              <select value={action} onChange={(event) => openAction(event.target.value as ActionKind)}>
                <option value="build" disabled={!actionAvailable('build')}>빌드 요청 · 미연결</option><option value="register_artifact" disabled={!actionAvailable('register_artifact')}>아티팩트 등록 · 미연결</option><option value="create_canary" disabled={!actionAvailable('create_canary') || liveRolloutExists}>Canary 생성</option><option value="advance_rollout" disabled={!actionAvailable('advance_rollout')}>Rollout 진행</option><option value="pause_rollout" disabled={!actionAvailable('pause_rollout')}>Rollout 일시정지</option><option value="rollback_rollout" disabled={!actionAvailable('rollback_rollout')}>Rollout 롤백</option><option value="complete_rollback" disabled={!actionAvailable('complete_rollback')}>Rollback 완료 확인</option>
              </select>
            </label>
            <label>Idempotency key<input value={idempotencyKey} onChange={(event) => setIdempotencyKey(event.target.value)} required minLength={16} maxLength={128} /></label>
            {(action === 'build') && <><label>Source commit<input value={sourceCommit} onChange={(event) => setSourceCommit(event.target.value)} required pattern="[a-f0-9]{40}|[a-f0-9]{64}" /></label><label>Source tree<input value={sourceTree} onChange={(event) => setSourceTree(event.target.value)} required pattern="[a-f0-9]{40}|[a-f0-9]{64}" /></label><label>테스트 프로필<select value={testProfile} onChange={(event) => setTestProfile(event.target.value as 'crawler' | 'crawler_full')}><option value="crawler">crawler</option><option value="crawler_full">crawler_full</option></select></label></>}
            {(action === 'register_artifact') && <><label>Build request UUID<input value={buildRequestId} onChange={(event) => setBuildRequestId(event.target.value)} required /></label><label>Artifact digest<input value={artifactDigest} onChange={(event) => setArtifactDigest(event.target.value)} required pattern="[a-f0-9]{64}" /></label><label>코드 버전<input value={codeVersion} onChange={(event) => setCodeVersion(event.target.value)} required maxLength={200} /></label><label>설정 리비전<input value={configRevision} onChange={(event) => setConfigRevision(event.target.value)} required maxLength={255} /></label></>}
            {(action === 'create_canary') && <><label>신규 Rollout UUID<input value={formRolloutId} readOnly required /><small className="form-note">충돌과 수동 오타를 막기 위해 브라우저에서 안전하게 자동 생성합니다.</small></label><label>대상 Artifact digest<input value={artifactDigest} onChange={(event) => setArtifactDigest(event.target.value)} required pattern="[a-f0-9]{64}" /></label><label>Baseline digest<input value={baselineDigest} onChange={(event) => setBaselineDigest(event.target.value)} required pattern="[a-f0-9]{64}" /></label><label>전체 Rollout Worker keys<textarea value={workersText} onChange={(event) => setWorkersText(event.target.value)} required placeholder="crawler-worker-a, crawler-worker-b" /><small className="form-note">Canary만이 아니라 비활성 Worker를 포함한 검토된 전체 fleet을 입력합니다.</small></label></>}
            {['advance_rollout', 'pause_rollout', 'rollback_rollout', 'complete_rollback'].includes(action) && <><label>Rollout UUID<input value={formRolloutId} onChange={(event) => setFormRolloutId(event.target.value)} required /></label><label>현재 기대 세대<input type="number" min={1} value={expectedGeneration} onChange={(event) => setExpectedGeneration(Number(event.target.value))} required /></label></>}
            {action === 'advance_rollout' && <><label>진행 단계<select value={rolloutPhase} onChange={(event) => setRolloutPhase(event.target.value as RolloutPhase)}><option value="rolling">rolling</option><option value="complete">complete</option></select></label>{rolloutPhase === 'rolling' && <label>대상 Worker keys<textarea value={targetWorkersText} onChange={(event) => setTargetWorkersText(event.target.value)} required placeholder="crawler-worker-a, crawler-worker-b" /></label>}</>}
            {action === 'create_canary' && <label>기대 세대<input type="number" min={1} value={expectedGeneration} onChange={(event) => setExpectedGeneration(Number(event.target.value))} required /></label>}
            <label>변경 사유<textarea value={reason} onChange={(event) => setReason(event.target.value)} required minLength={3} maxLength={500} /></label>
            <p className="form-note">정확히 아래 문구를 입력해야 합니다. Worker 목록은 정렬 후 SHA-256 digest로 문구에 결박됩니다.<br /><code className="confirmation-code">{requiredConfirmation || 'Worker 목록을 입력하면 확인 문구가 생성됩니다.'}</code></p>
            <label>확인 문구<input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} required /></label>
            <button className="button primary" type="submit" disabled={!actionAvailable(action) || actionMutation.isPending || !requiredConfirmation || confirmation !== requiredConfirmation}>{actionMutation.isPending ? '대기열 기록 중…' : '감사 대기열에 요청'}</button>
          </form>
        </DetailPanel>
      )}
    </>
  );
}
