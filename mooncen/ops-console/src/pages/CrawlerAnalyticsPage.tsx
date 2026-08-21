import { useQuery } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { useMemo, useState } from 'react';
import { Link } from 'react-router';
import { opsApi } from '../api';
import DataTable from '../components/DataTable';
import StatusBadge from '../components/StatusBadge';
import { DefinitionList, DetailPanel, PageHeader, QueryState, StatCard } from '../components/Ui';
import { useOpsSession } from '../context';
import { formatDate, formatNumber } from '../utils';

type Reason = { code?: string; message?: string; section?: string } & Record<string, unknown>;
type Component<T extends Record<string, unknown> = Record<string, unknown>> = {
  available: boolean;
  has_data: boolean | null;
  reasons: Reason[];
} & T;
type Section = {
  available: boolean;
  complete: boolean;
  has_data: boolean | null;
  reasons: Reason[];
  components: Record<string, Component>;
};
type AnalyticsPayload = {
  schema_version?: number;
  available: boolean;
  complete: boolean;
  partial: boolean;
  environment: string;
  generated_at: string;
  window_hours: number;
  heartbeat_timeout_seconds: number;
  reasons: Reason[];
  deployment: Section;
  collection: Section;
  providers: Section;
  quality: Section;
  workers: Section;
  queue: Section;
  correlations: Section;
};
type ProviderRow = Record<string, unknown> & {
  provider: string;
  run_count: number;
  successful_runs: number;
  failed_runs: number;
  collected_count: number;
  new_count: number;
  updated_count: number;
  failed_item_count: number;
  success_rate?: number | null;
  last_run_at?: string | null;
};
type WorkerRow = Record<string, unknown> & {
  id: string;
  name: string;
  hostname: string;
  status: string;
  maintenance_mode: boolean;
  heartbeat_stale: boolean;
  health_state: string;
  last_seen_at?: string | null;
};
type VersionRow = Record<string, unknown> & {
  worker_key: string;
  cohort: string;
  generation: number;
  desired_code_version?: string | null;
  reported_code_version?: string | null;
  version_state: string;
  matches_desired_release: boolean;
  reported_at?: string | null;
};
type CorrelationGenerationRow = Record<string, unknown> & {
  rollout_id: string;
  generation: number;
  generation_started_at: string;
  code_versions: string[];
  attempt_count: number;
  failed_attempts: number;
  retried_tasks: number;
  lease_lost_attempts: number;
  duration_seconds: number;
  collected_count?: number | null;
  new_count?: number | null;
  updated_count?: number | null;
  invalid_courses?: number | null;
  deltas: Record<string, number | null>;
};
type CorrelationBatchRow = Record<string, unknown> & {
  id: string;
  status: string;
  scheduled_slot: string;
  providers: string[];
  generation?: number | null;
  attribution_state: string;
  collected_count?: number | null;
  new_count?: number | null;
  updated_count?: number | null;
  failed_item_count?: number | null;
  retry_attempts: number;
  lease_lost_attempts: number;
  duration_seconds: number;
  invalid_courses?: number | null;
};
type CorrelationQualityRow = Record<string, unknown> & {
  rollout_id: string;
  generation: number;
  providers: string[];
  average_score?: number | null;
  bad_courses: number;
  incomplete_courses: number;
  issue_count: number;
  critical_issues: number;
  blocked_sync_issues: number;
  deltas: Record<string, number | null>;
};
type CorrelationTaskRow = Record<string, unknown> & {
  task_key: string;
  provider: string;
  job_status: string;
  attempt_no?: number | null;
  attempt_status?: string | null;
  generation?: number | null;
  attribution_state: string;
  total_count?: number | null;
  new_count?: number | null;
  updated_count?: number | null;
  failed_count?: number | null;
  retry_count: number;
  lease_lost_attempts: number;
  attempt_duration_seconds?: number | null;
  error_code?: string | null;
};
type BatchDetailResponse = {
  available: boolean;
  item: Record<string, unknown> | null;
  tasks: CorrelationTaskRow[] | null;
  total_tasks: number | null;
  truncated: boolean | null;
  attribution: Component;
  reasons: Reason[];
};

const REASON_LABELS: Record<string, string> = {
  crawler_control_database_not_configured: 'crawler-control 전용 읽기 연결이 아직 설정되지 않았습니다.',
  crawler_control_database_unavailable: 'crawler-control 데이터베이스에 연결할 수 없습니다.',
  crawler_control_readonly_boundary_unavailable: 'crawler-control 읽기 전용 트랜잭션을 시작할 수 없습니다.',
  crawler_control_database_marker_mismatch: '연결된 데이터베이스가 검증된 crawler-control DB가 아닙니다.',
  crawler_control_database_marker_unreadable: 'crawler-control DB 식별 정보를 확인할 수 없습니다.',
  crawler_api_environment_binding_mismatch: 'Ops 환경과 crawler-control API 계정의 환경이 일치하지 않습니다.',
  schema_inventory_unavailable: 'crawler-control 스키마 상태를 확인할 수 없습니다.',
  crawler_correlation_schema_unavailable: '크롤러 실행 상관관계 스키마가 준비되지 않았습니다.',
  environment_dimension_unavailable: '환경 구분 정보를 확인할 수 없습니다.',
  generation_attribution_evidence_unavailable: '릴리스 세대 귀속 증거가 준비되지 않았습니다.',
  generation_quality_attribution_unavailable: '릴리스 세대별 품질 귀속 증거가 준비되지 않았습니다.',
  validation_batch_generation_attribution_excluded: '세대 귀속이 불확실한 검증 배치는 집계에서 제외했습니다.',
  missing_table: '필요한 crawler-control 테이블이 없습니다.',
  missing_columns: '필요한 crawler-control 컬럼이 없습니다.',
  query_unavailable: 'crawler-control 분석 쿼리를 완료하지 못했습니다.',
};

function reasonText(reasons: Reason[] | undefined): string {
  if (!reasons?.length) return '필요한 중앙 데이터 소스가 연결되지 않았습니다.';
  return reasons.map((reason) => {
    const code = typeof reason.code === 'string' ? reason.code : '';
    return REASON_LABELS[code] || reason.message || code || '연동 사유 미확인';
  }).join(' · ');
}

function metric(source: Record<string, unknown> | null | undefined, key: string): number | string {
  const value = source?.[key];
  return typeof value === 'number' ? value : '-';
}

function deltaText(value: number | null | undefined): string {
  if (value == null) return '-';
  return `${value > 0 ? '+' : ''}${formatNumber(value)}`;
}

function evidenceNumber(value: number | null | undefined): string {
  return value == null ? '-' : formatNumber(value);
}

function AnalyticsComponentState({ component, emptyLabel = '선택한 기간에 실제 데이터가 없습니다.' }: { component?: Component; emptyLabel?: string }) {
  if (!component || component.available === false) {
    return <div className="state-panel analytics-unavailable">{reasonText(component?.reasons)}</div>;
  }
  if (component.has_data === false) return <div className="state-panel">{emptyLabel}</div>;
  return null;
}

export default function CrawlerAnalyticsPage() {
  const session = useOpsSession();
  const [windowHours, setWindowHours] = useState(24);
  const [selectedBatchId, setSelectedBatchId] = useState('');
  const analytics = useQuery({
    queryKey: ['crawler-analytics', session.environment, windowHours],
    queryFn: () => opsApi<AnalyticsPayload>(`/crawlers/analytics?${new URLSearchParams({
      environment: session.environment,
      window_hours: String(windowHours),
      correlation_limit: '25',
    })}`),
    refetchInterval: 30_000,
  });
  const batchDetail = useQuery({
    queryKey: ['crawler-analytics-batch', session.environment, selectedBatchId],
    queryFn: () => opsApi<BatchDetailResponse>(`/crawlers/analytics/batches/${selectedBatchId}?${new URLSearchParams({
      environment: session.environment,
      task_limit: '100',
      task_offset: '0',
    })}`),
    enabled: Boolean(selectedBatchId),
  });
  const data = analytics.data;
  const rollout = data?.deployment.components.rollout as Component<{ latest?: Record<string, unknown> | null }> | undefined;
  const versions = data?.deployment.components.versions as Component<{ summary?: Record<string, unknown> | null; items?: VersionRow[] | null }> | undefined;
  const runs = data?.collection.components.runs as Component<{ totals?: Record<string, unknown> | null }> | undefined;
  const batches = data?.collection.components.batches as Component<{ outcomes?: Record<string, unknown> | null }> | undefined;
  const validation = data?.collection.components.validation as Component<{ totals?: Record<string, unknown> | null }> | undefined;
  const providerCollection = data?.providers.components.collection as Component<{ items?: ProviderRow[] | null; total?: number | null; truncated?: boolean | null }> | undefined;
  const score = data?.quality.components.scores as Component<{ summary?: Record<string, unknown> | null; providers?: Array<Record<string, unknown>> | null }> | undefined;
  const issues = data?.quality.components.issues as Component<{ summary?: Record<string, unknown> | null }> | undefined;
  const workerHealth = data?.workers.components.health as Component<{ summary?: Record<string, unknown> | null; items?: WorkerRow[] | null }> | undefined;
  const queueHealth = data?.queue.components.health as Component<{ metrics?: Record<string, unknown> | null }> | undefined;
  const generationCorrelations = data?.correlations.components.generations as Component<{ items?: CorrelationGenerationRow[] | null; total?: number | null; truncated?: boolean | null }> | undefined;
  const batchCorrelations = data?.correlations.components.batches as Component<{ items?: CorrelationBatchRow[] | null; total?: number | null; truncated?: boolean | null }> | undefined;
  const attribution = data?.correlations.components.attribution as Component<{ summary?: Record<string, unknown> | null }> | undefined;
  const correlationQuality = data?.correlations.components.quality as Component<{ items?: CorrelationQualityRow[] | null; total?: number | null; truncated?: boolean | null }> | undefined;

  const providerColumns = useMemo<ColumnDef<ProviderRow>[]>(() => [
    { accessorKey: 'provider', header: 'Provider' },
    { accessorKey: 'run_count', header: '실행', cell: ({ row }) => formatNumber(row.original.run_count) },
    { accessorKey: 'success_rate', header: '성공률', cell: ({ row }) => row.original.success_rate == null ? '-' : `${row.original.success_rate.toFixed(1)}%` },
    { accessorKey: 'failed_runs', header: '실패 실행', cell: ({ row }) => formatNumber(row.original.failed_runs) },
    { accessorKey: 'collected_count', header: '수집', cell: ({ row }) => formatNumber(row.original.collected_count) },
    { accessorKey: 'new_count', header: '신규', cell: ({ row }) => formatNumber(row.original.new_count) },
    { accessorKey: 'updated_count', header: '변경', cell: ({ row }) => formatNumber(row.original.updated_count) },
    { accessorKey: 'failed_item_count', header: '항목 실패', cell: ({ row }) => formatNumber(row.original.failed_item_count) },
    { accessorKey: 'last_run_at', header: '최근 실행', cell: ({ row }) => formatDate(row.original.last_run_at) },
  ], []);
  const workerColumns = useMemo<ColumnDef<WorkerRow>[]>(() => [
    { accessorKey: 'name', header: 'Worker' },
    { accessorKey: 'hostname', header: 'Host' },
    { accessorKey: 'health_state', header: '상태', cell: ({ row }) => <StatusBadge status={row.original.health_state} /> },
    { accessorKey: 'maintenance_mode', header: '점검', cell: ({ row }) => row.original.maintenance_mode ? '점검 중' : '-' },
    { accessorKey: 'heartbeat_stale', header: 'Heartbeat', cell: ({ row }) => row.original.heartbeat_stale ? '지연' : '정상' },
    { accessorKey: 'last_seen_at', header: '최근 연결', cell: ({ row }) => formatDate(row.original.last_seen_at) },
  ], []);
  const versionColumns = useMemo<ColumnDef<VersionRow>[]>(() => [
    { accessorKey: 'worker_key', header: 'Worker' },
    { accessorKey: 'cohort', header: 'Cohort' },
    { accessorKey: 'generation', header: '목표 세대' },
    { accessorKey: 'version_state', header: '버전 상태', cell: ({ row }) => <StatusBadge status={row.original.version_state} /> },
    { accessorKey: 'desired_code_version', header: '목표 버전' },
    { accessorKey: 'reported_code_version', header: '보고 버전' },
    { accessorKey: 'reported_at', header: '최근 보고', cell: ({ row }) => formatDate(row.original.reported_at) },
  ], []);
  const generationColumns = useMemo<ColumnDef<CorrelationGenerationRow>[]>(() => [
    { accessorKey: 'generation', header: '세대' },
    { id: 'versions', header: '릴리스', cell: ({ row }) => row.original.code_versions.join(', ') || '-' },
    { accessorKey: 'attempt_count', header: '시도', cell: ({ row }) => formatNumber(row.original.attempt_count) },
    { accessorKey: 'collected_count', header: '수집', cell: ({ row }) => evidenceNumber(row.original.collected_count) },
    { accessorKey: 'new_count', header: '신규', cell: ({ row }) => evidenceNumber(row.original.new_count) },
    { accessorKey: 'updated_count', header: '변경', cell: ({ row }) => <span>{evidenceNumber(row.original.updated_count)} <small>({deltaText(row.original.deltas?.updated_count)})</small></span> },
    { accessorKey: 'failed_attempts', header: '실패 시도', cell: ({ row }) => formatNumber(row.original.failed_attempts) },
    { accessorKey: 'retried_tasks', header: '재시도 Task', cell: ({ row }) => formatNumber(row.original.retried_tasks) },
    { accessorKey: 'lease_lost_attempts', header: 'Lease loss', cell: ({ row }) => formatNumber(row.original.lease_lost_attempts) },
    { accessorKey: 'duration_seconds', header: '실행 초', cell: ({ row }) => formatNumber(row.original.duration_seconds) },
    { accessorKey: 'invalid_courses', header: '무효', cell: ({ row }) => <span>{evidenceNumber(row.original.invalid_courses)} <small>({deltaText(row.original.deltas?.invalid_courses)})</small></span> },
  ], []);
  const batchColumns = useMemo<ColumnDef<CorrelationBatchRow>[]>(() => [
    { accessorKey: 'scheduled_slot', header: '배치 시각', cell: ({ row }) => formatDate(row.original.scheduled_slot) },
    { accessorKey: 'status', header: '상태', cell: ({ row }) => <StatusBadge status={row.original.status} /> },
    { accessorKey: 'generation', header: '세대', cell: ({ row }) => row.original.generation ?? '-' },
    { accessorKey: 'attribution_state', header: '귀속', cell: ({ row }) => <StatusBadge status={row.original.attribution_state} /> },
    { id: 'providers', header: 'Provider', cell: ({ row }) => row.original.providers?.join(', ') || '-' },
    { accessorKey: 'collected_count', header: '수집', cell: ({ row }) => evidenceNumber(row.original.collected_count) },
    { accessorKey: 'new_count', header: '신규', cell: ({ row }) => evidenceNumber(row.original.new_count) },
    { accessorKey: 'updated_count', header: '변경', cell: ({ row }) => evidenceNumber(row.original.updated_count) },
    { accessorKey: 'retry_attempts', header: '재시도', cell: ({ row }) => formatNumber(row.original.retry_attempts) },
    { accessorKey: 'lease_lost_attempts', header: 'Lease loss', cell: ({ row }) => formatNumber(row.original.lease_lost_attempts) },
    { accessorKey: 'invalid_courses', header: '무효', cell: ({ row }) => row.original.invalid_courses == null ? '-' : formatNumber(row.original.invalid_courses) },
  ], []);
  const correlationQualityColumns = useMemo<ColumnDef<CorrelationQualityRow>[]>(() => [
    { accessorKey: 'generation', header: '세대' },
    { id: 'providers', header: 'Provider', cell: ({ row }) => row.original.providers.join(', ') || '-' },
    { accessorKey: 'average_score', header: '평균 점수', cell: ({ row }) => row.original.average_score == null ? '-' : `${row.original.average_score} (${deltaText(row.original.deltas?.average_score)})` },
    { accessorKey: 'bad_courses', header: 'Bad', cell: ({ row }) => formatNumber(row.original.bad_courses) },
    { accessorKey: 'incomplete_courses', header: '미완성', cell: ({ row }) => formatNumber(row.original.incomplete_courses) },
    { accessorKey: 'issue_count', header: '품질 이슈', cell: ({ row }) => `${formatNumber(row.original.issue_count)} (${deltaText(row.original.deltas?.issue_count)})` },
    { accessorKey: 'critical_issues', header: 'Critical', cell: ({ row }) => formatNumber(row.original.critical_issues) },
    { accessorKey: 'blocked_sync_issues', header: '동기화 차단', cell: ({ row }) => formatNumber(row.original.blocked_sync_issues) },
  ], []);
  const taskColumns = useMemo<ColumnDef<CorrelationTaskRow>[]>(() => [
    { accessorKey: 'provider', header: 'Provider' },
    { accessorKey: 'task_key', header: 'Task' },
    { accessorKey: 'job_status', header: 'Job', cell: ({ row }) => <StatusBadge status={row.original.job_status} /> },
    { accessorKey: 'attempt_status', header: '시도', cell: ({ row }) => <StatusBadge status={row.original.attempt_status} /> },
    { accessorKey: 'generation', header: '세대', cell: ({ row }) => row.original.generation ?? '-' },
    { accessorKey: 'attribution_state', header: '귀속', cell: ({ row }) => <StatusBadge status={row.original.attribution_state} /> },
    { accessorKey: 'total_count', header: '수집', cell: ({ row }) => row.original.total_count == null ? '-' : formatNumber(row.original.total_count) },
    { accessorKey: 'new_count', header: '신규', cell: ({ row }) => row.original.new_count == null ? '-' : formatNumber(row.original.new_count) },
    { accessorKey: 'updated_count', header: '변경', cell: ({ row }) => row.original.updated_count == null ? '-' : formatNumber(row.original.updated_count) },
    { accessorKey: 'retry_count', header: '재시도', cell: ({ row }) => formatNumber(row.original.retry_count) },
    { accessorKey: 'lease_lost_attempts', header: 'Lease loss', cell: ({ row }) => formatNumber(row.original.lease_lost_attempts) },
    { accessorKey: 'error_code', header: '오류', cell: ({ row }) => row.original.error_code || '-' },
  ], []);

  return (
    <>
      <PageHeader
        eyebrow="CENTRAL CRAWLER OPERATIONS"
        title="Crawler Analytics"
        description="중앙 제어 DB 기준으로 배포 수렴, 수집 결과, 검증 품질, Worker와 작업 대기열을 같은 시간 창에서 분석합니다."
        actions={(
          <>
            <label className="analytics-window">분석 기간<select aria-label="분석 기간" value={windowHours} onChange={(event) => setWindowHours(Number(event.target.value))}><option value={24}>최근 24시간</option><option value={72}>최근 72시간</option><option value={168}>최근 7일</option></select></label>
            <Link className="button subtle" to="/crawler-improvements">개선 큐</Link>
            <Link className="button subtle" to="/crawler-releases">릴리스 관리</Link>
          </>
        )}
      />
      <QueryState loading={analytics.isLoading} error={analytics.error} />
      {data?.available === false && (
        <div className="deploy-blockers crawler-control-unavailable" role="alert">
          <strong>중앙 크롤러 분석 데이터에 연결되지 않았습니다.</strong>
          <span>{reasonText(data.reasons)} 이 상태는 모든 지표가 0이라는 뜻이 아닙니다.</span>
        </div>
      )}
      {data?.available && (
        <div className={data.complete ? 'deploy-ready analytics-readiness' : 'deploy-blockers analytics-readiness'}>
          <strong>{data.complete ? '중앙 분석 스냅샷 완전' : '중앙 분석 스냅샷 일부만 사용 가능'}</strong>
          <span>{data.partial ? reasonText(data.reasons) : `${data.environment.toUpperCase()} · ${formatDate(data.generated_at)} 생성`}</span>
        </div>
      )}

      {data?.available && (
        <>
          <section className="panel">
            <header className="section-header"><div><h2>작업 대기열</h2><small>준비·할당·실행 작업과 만료 lease/dead letter를 구분합니다.</small></div></header>
            <AnalyticsComponentState component={queueHealth} />
            {queueHealth?.available && queueHealth.has_data && queueHealth.metrics ? (
              <div className="stats-grid">
                <StatCard label="준비 작업" value={formatNumber(metric(queueHealth.metrics, 'ready_jobs'))} tone={Number(queueHealth.metrics.ready_jobs) ? 'warn' : 'neutral'} />
                <StatCard label="지연 작업" value={formatNumber(metric(queueHealth.metrics, 'delayed_jobs'))} />
                <StatCard label="실행 중" value={formatNumber(metric(queueHealth.metrics, 'running_jobs'))} />
                <StatCard label="만료 Lease" value={formatNumber(metric(queueHealth.metrics, 'expired_leases'))} tone={Number(queueHealth.metrics.expired_leases) ? 'bad' : 'good'} />
                <StatCard label="Dead letter" value={formatNumber(metric(queueHealth.metrics, 'dead_lettered_jobs'))} tone={Number(queueHealth.metrics.dead_lettered_jobs) ? 'bad' : 'good'} />
                <StatCard label="최장 대기(초)" value={formatNumber(metric(queueHealth.metrics, 'oldest_ready_age_seconds'))} />
              </div>
            ) : null}
          </section>

          <div className="analytics-columns">
            <section className="panel">
              <header className="section-header"><div><h2>수집 결과</h2><small>최근 {data.window_hours}시간 중앙 실행 합계</small></div></header>
              <AnalyticsComponentState component={runs} />
              {runs?.available && runs.has_data && runs.totals ? <div className="analytics-metric-grid"><StatCard label="실행" value={formatNumber(metric(runs.totals, 'run_count'))} /><StatCard label="성공" value={formatNumber(metric(runs.totals, 'successful_runs'))} tone="good" /><StatCard label="실패" value={formatNumber(metric(runs.totals, 'failed_runs'))} tone={Number(runs.totals.failed_runs) ? 'bad' : 'neutral'} /><StatCard label="수집" value={formatNumber(metric(runs.totals, 'collected_count'))} /><StatCard label="신규" value={formatNumber(metric(runs.totals, 'new_count'))} /><StatCard label="변경" value={formatNumber(metric(runs.totals, 'updated_count'))} /></div> : null}
            </section>
            <section className="panel">
              <header className="section-header"><div><h2>배치 · 승격 검증</h2><small>수집 배치 결과와 staging 검증 상태</small></div></header>
              <AnalyticsComponentState component={batches} />
              <AnalyticsComponentState component={validation} />
              {batches?.available && batches.has_data && batches.outcomes ? <div className="analytics-metric-grid"><StatCard label="배치" value={formatNumber(metric(batches.outcomes, 'batch_count'))} /><StatCard label="성공 배치" value={formatNumber(metric(batches.outcomes, 'successful_batches'))} tone="good" /><StatCard label="실패 배치" value={formatNumber(metric(batches.outcomes, 'failed_batches'))} tone={Number(batches.outcomes.failed_batches) ? 'bad' : 'neutral'} /><StatCard label="진행 배치" value={formatNumber(metric(batches.outcomes, 'active_batches'))} /></div> : null}
              {validation?.available && validation.has_data && validation.totals ? <div className="analytics-metric-grid analytics-validation-grid"><StatCard label="봉인 배치" value={formatNumber(metric(validation.totals, 'sealed_batch_count'))} /><StatCard label="유효 강좌" value={formatNumber(metric(validation.totals, 'valid_courses'))} tone="good" /><StatCard label="무효 강좌" value={formatNumber(metric(validation.totals, 'invalid_courses'))} tone={Number(validation.totals.invalid_courses) ? 'bad' : 'neutral'} /><StatCard label="승인 보류" value={formatNumber(metric(validation.totals, 'held_for_approval_batches'))} tone={Number(validation.totals.held_for_approval_batches) ? 'warn' : 'neutral'} /></div> : null}
            </section>
          </div>

          <section className="panel">
            <header className="section-header"><div><h2>Provider별 수집</h2><small>실패 실행과 낮은 성공률을 우선 정렬한 중앙 수집 결과입니다.</small></div></header>
            <AnalyticsComponentState component={providerCollection} />
            {providerCollection?.available && providerCollection.items?.length ? <DataTable data={providerCollection.items} columns={providerColumns} exportName="crawler-provider-analytics.csv" /> : null}
          </section>

          <section className="panel">
            <header className="section-header"><div><h2>릴리스 세대별 수집 상관관계</h2><small>불변 Worker 스냅샷과 실행 아티팩트·설정 identity가 정확히 일치한 중앙 증거만 집계합니다.</small></div></header>
            <AnalyticsComponentState component={attribution} emptyLabel="선택한 기간에 귀속할 실행 시도가 없습니다." />
            {attribution?.summary ? (
              <div className="analytics-metric-grid">
                <StatCard label="전체 시도" value={formatNumber(metric(attribution.summary, 'total_attempts'))} />
                <StatCard label="세대 정확 귀속" value={formatNumber(metric(attribution.summary, 'attributed_attempts'))} tone="good" />
                <StatCard label="레거시 미귀속" value={formatNumber(metric(attribution.summary, 'legacy_unattributed_attempts'))} tone={Number(attribution.summary.legacy_unattributed_attempts) ? 'warn' : 'neutral'} />
                <StatCard label="증거 불일치 거부" value={formatNumber(metric(attribution.summary, 'rejected_mismatched_attempts'))} tone={Number(attribution.summary.rejected_mismatched_attempts) ? 'bad' : 'neutral'} />
              </div>
            ) : null}
            <AnalyticsComponentState component={generationCorrelations} emptyLabel="선택한 기간에 릴리스 세대 증거가 없습니다." />
            {generationCorrelations?.available && generationCorrelations.has_data && generationCorrelations.items?.length ? (
              <DataTable data={generationCorrelations.items} columns={generationColumns} exportName="crawler-release-generation-correlation.csv" />
            ) : null}
          </section>

          <section className="panel">
            <header className="section-header"><div><h2>중앙 수집 배치</h2><small>최근 배치의 duration·retry·lease loss·검증 결과를 확인하고 행을 선택해 중앙 증거를 상세 조회합니다.</small></div></header>
            <AnalyticsComponentState component={batchCorrelations} emptyLabel="선택한 기간에 중앙 수집 배치가 없습니다." />
            {batchCorrelations?.available && batchCorrelations.has_data && batchCorrelations.items?.length ? (
              <DataTable
                data={batchCorrelations.items}
                columns={batchColumns}
                exportName="crawler-central-batch-correlation.csv"
                onRowClick={(row) => setSelectedBatchId(row.id)}
              />
            ) : null}
          </section>

          <section className="panel">
            <header className="section-header"><div><h2>세대별 품질·검증 변화</h2><small>현재 품질 행에는 불변 배치·시도 연결이 없어 릴리스 세대에 추정 귀속하지 않습니다. 중앙에 정확한 연결 증거가 생길 때까지 이 영역은 미제공으로 표시됩니다.</small></div></header>
            <AnalyticsComponentState component={correlationQuality} emptyLabel="선택한 기간에 세대별 품질 상관 증거가 없습니다." />
            {correlationQuality?.available && correlationQuality.has_data && correlationQuality.items?.length ? (
              <DataTable data={correlationQuality.items} columns={correlationQualityColumns} exportName="crawler-release-quality-correlation.csv" />
            ) : null}
          </section>

          <div className="analytics-columns">
            <section className="panel">
              <header className="section-header"><div><h2>데이터 품질</h2><small>현재 중앙 control DB 범위의 점수와 동기화 차단 이슈</small></div></header>
              <AnalyticsComponentState component={score} />
              <AnalyticsComponentState component={issues} />
              {score?.available && score.has_data && score.summary ? <div className="analytics-metric-grid"><StatCard label="평균 점수" value={metric(score.summary, 'average_score')} /><StatCard label="Good" value={formatNumber(metric(score.summary, 'good_courses'))} tone="good" /><StatCard label="Bad" value={formatNumber(metric(score.summary, 'bad_courses'))} tone={Number(score.summary.bad_courses) ? 'bad' : 'neutral'} /><StatCard label="미완성" value={formatNumber(metric(score.summary, 'incomplete_courses'))} tone={Number(score.summary.incomplete_courses) ? 'warn' : 'neutral'} /></div> : null}
              {issues?.available && issues.has_data && issues.summary ? <p className="form-note">활성 이슈 {formatNumber(metric(issues.summary, 'active_issues'))}건 · Critical {formatNumber(metric(issues.summary, 'active_critical_issues'))}건 · 동기화 차단 {formatNumber(metric(issues.summary, 'blocked_sync_issues'))}건</p> : null}
            </section>
            <section className="panel">
              <header className="section-header"><div><h2>Worker 상태</h2><small>Heartbeat 제한 {formatNumber(data.heartbeat_timeout_seconds)}초</small></div></header>
              <AnalyticsComponentState component={workerHealth} />
              {workerHealth?.available && workerHealth.has_data && workerHealth.summary ? <div className="analytics-metric-grid"><StatCard label="전체" value={formatNumber(metric(workerHealth.summary, 'worker_count'))} /><StatCard label="정상" value={formatNumber(metric(workerHealth.summary, 'healthy_workers'))} tone="good" /><StatCard label="지연" value={formatNumber(metric(workerHealth.summary, 'stale_workers'))} tone={Number(workerHealth.summary.stale_workers) ? 'bad' : 'neutral'} /><StatCard label="점검" value={formatNumber(metric(workerHealth.summary, 'maintenance_workers'))} /></div> : null}
            </section>
          </div>

          {workerHealth?.available && workerHealth.items?.length ? <section className="panel"><header className="section-header"><div><h2>Worker 상세</h2><small>중앙 서버에 바인딩된 수집 Worker heartbeat</small></div></header><DataTable data={workerHealth.items} columns={workerColumns} exportName="crawler-worker-analytics.csv" /></section> : null}

          <section className="panel">
            <header className="section-header"><div><h2>배포 수렴</h2><small>최신 Rollout과 Worker별 목표/보고 버전을 비교합니다.</small></div><Link className="button subtle" to="/crawler-releases">릴리스 상세</Link></header>
            <AnalyticsComponentState component={rollout} />
            <AnalyticsComponentState component={versions} />
            {rollout?.available && rollout.has_data && rollout.latest ? <div className="release-latest"><StatusBadge status={String(rollout.latest.status)} /><strong>세대 {String(rollout.latest.rollout_epoch)}</strong><span>{String(rollout.latest.code_version || '-')}</span><small>{formatDate(rollout.latest.started_at || rollout.latest.created_at)}</small></div> : null}
            {versions?.available && versions.has_data && versions.summary ? <div className="analytics-metric-grid"><StatCard label="목표 Worker" value={formatNumber(metric(versions.summary, 'desired_workers'))} /><StatCard label="현재 버전 준비" value={formatNumber(metric(versions.summary, 'ready_current_workers'))} tone="good" /><StatCard label="구버전" value={formatNumber(metric(versions.summary, 'outdated_workers'))} tone={Number(versions.summary.outdated_workers) ? 'warn' : 'neutral'} /><StatCard label="실패" value={formatNumber(metric(versions.summary, 'failed_workers'))} tone={Number(versions.summary.failed_workers) ? 'bad' : 'neutral'} /></div> : null}
            {versions?.available && versions.items?.length ? <DataTable data={versions.items} columns={versionColumns} exportName="crawler-version-convergence.csv" /> : null}
          </section>
        </>
      )}
      {selectedBatchId && (
        <DetailPanel title="중앙 수집 배치 상세" onClose={() => setSelectedBatchId('')}>
          <QueryState loading={batchDetail.isLoading} error={batchDetail.error} />
          {batchDetail.data?.available === false ? (
            <div className="state-panel analytics-unavailable" role="alert">
              {reasonText(batchDetail.data.reasons)} 이 상태는 배치 지표가 0이라는 뜻이 아닙니다.
            </div>
          ) : null}
          {batchDetail.data?.available && batchDetail.data.item ? (
            <>
              <DefinitionList value={batchDetail.data.item} />
              <h3>릴리스 귀속</h3>
              <AnalyticsComponentState component={batchDetail.data.attribution} emptyLabel="아직 실행 시도가 없어 귀속 증거가 없습니다." />
              <p className="form-note">
                중앙 Task {batchDetail.data.total_tasks == null ? '-' : formatNumber(batchDetail.data.total_tasks)}건
                {batchDetail.data.truncated ? ' · 일부만 표시' : ''}
              </p>
              {batchDetail.data.tasks?.length ? (
                <DataTable data={batchDetail.data.tasks} columns={taskColumns} exportName={`crawler-batch-${selectedBatchId}-tasks.csv`} />
              ) : batchDetail.data.tasks ? (
                <div className="state-panel">이 배치에 등록된 중앙 Task가 없습니다.</div>
              ) : null}
            </>
          ) : null}
        </DetailPanel>
      )}
    </>
  );
}
