import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { useCallback, useMemo, useState, type FormEvent } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { opsApi } from '../api';
import DataTable from '../components/DataTable';
import StatusBadge from '../components/StatusBadge';
import { DefinitionList, DetailPanel, PageHeader, QueryState } from '../components/Ui';
import { useOpsSession } from '../context';
import type { CrawlerRun, PageResponse } from '../types';
import { formatDate, formatNumber } from '../utils';

type CrawlerSummary = Record<string, unknown> & {
  provider: string;
  crawler_name: string;
  content_type: string;
  status: string;
  last_run_status: string;
  last_run_trigger: string;
  can_run: boolean;
  run_blocked_reason?: string | null;
};

function runTriggerLabel(trigger: unknown): string {
  if (trigger === 'local_schedule') return '자동';
  if (trigger === 'standalone') return '직접 실행';
  if (trigger === 'manual') return '수동';
  return '확인 불가';
}

function confirmProduction(environment: string): boolean {
  if (environment !== 'production') return window.confirm('크롤러 작업을 대기열에 등록할까요?');
  return window.prompt('운영 크롤러 실행 확인을 위해 MOONCEN-PRODUCTION을 입력하세요.') === 'MOONCEN-PRODUCTION';
}

function providerRunPayload(provider: string, contentType: string): Record<string, unknown> {
  const supportedType = ['culture_center', 'experience', 'education'].includes(contentType)
    ? contentType
    : 'all';
  return {
    scope: 'provider',
    provider: provider.trim().toUpperCase(),
    content_type: supportedType,
    run_mode: 'apply',
    compare_existing: true,
    max_retries: 1,
    concurrency: 1,
  };
}

export default function CrawlersPage() {
  const session = useOpsSession();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { id } = useParams();
  const [searchParams] = useSearchParams();
  const requestedProvider = (searchParams.get('provider') || '').trim().slice(0, 100);
  const crawlerRunsPath = requestedProvider
    ? `/crawlers/runs?limit=100&provider=${encodeURIComponent(requestedProvider)}`
    : '/crawlers/runs?limit=100';
  const [provider, setProvider] = useState(requestedProvider);
  const [contentType, setContentType] = useState('all');
  const [showRun, setShowRun] = useState(false);
  const [showProbe, setShowProbe] = useState(false);
  const [probeUrl, setProbeUrl] = useState('');
  const crawlers = useQuery({
    queryKey: ['crawlers'],
    queryFn: () => opsApi<{ available: boolean; items: CrawlerSummary[]; total: number }>('/crawlers'),
    refetchInterval: 30_000,
  });
  const runs = useQuery({
    queryKey: ['crawler-runs', requestedProvider],
    queryFn: () => opsApi<PageResponse<CrawlerRun>>(crawlerRunsPath),
    refetchInterval: 15_000,
  });
  const detail = useQuery({
    queryKey: ['crawler-run', id],
    queryFn: () => opsApi<Record<string, unknown>>(`/crawlers/runs/${id}`),
    enabled: Boolean(id),
    refetchInterval: id ? 5_000 : false,
  });
  const errors = useQuery({
    queryKey: ['crawler-run-errors', id],
    queryFn: () => opsApi<{ available: boolean; items: Array<Record<string, unknown>> }>(`/crawlers/runs/${id}/errors`),
    enabled: Boolean(id),
  });
  const runJobId = detail.data?.job_id ? String(detail.data.job_id) : '';
  const runLogs = useQuery({
    queryKey: ['crawler-run-logs', id, runJobId],
    queryFn: () =>
      opsApi<{ available: boolean; items: Array<Record<string, unknown>> }>(
        `/jobs/${runJobId}/logs?limit=1000&tail=true`,
      ),
    enabled: Boolean(id && runJobId),
    refetchInterval: id && ['queued', 'assigned', 'running'].includes(String(detail.data?.status)) ? 5_000 : false,
  });
  const runMutation = useMutation<
    { job: { id: string }; crawler_run: { id: string } },
    Error,
    Record<string, unknown>
  >({
    mutationFn: (body: Record<string, unknown>) =>
      opsApi<{ job: { id: string }; crawler_run: { id: string } }>('/crawlers/run', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: (result) => {
      setShowRun(false);
      void queryClient.invalidateQueries({ queryKey: ['crawlers'] });
      void queryClient.invalidateQueries({ queryKey: ['crawler-runs'] });
      void queryClient.invalidateQueries({ queryKey: ['jobs'] });
      navigate(`/crawlers/runs/${result.crawler_run.id}`);
    },
  });
  const probeMutation = useMutation({
    mutationFn: () =>
      opsApi<{ job: { id: string } }>('/crawlers/parser-probe', {
        method: 'POST',
        body: JSON.stringify({ url: probeUrl.trim(), timeout: 25 }),
      }),
    onSuccess: (result) => {
      setShowProbe(false);
      navigate(`/jobs/${result.job.id}`);
    },
  });
  const submitRun = (event: FormEvent) => {
    event.preventDefault();
    if (!provider.trim() || !confirmProduction(session.environment)) return;
    runMutation.mutate(providerRunPayload(provider, contentType));
  };
  const queueProviderRun = useCallback(
    (crawler: CrawlerSummary) => {
      if (!confirmProduction(session.environment)) return;
      runMutation.mutate(providerRunPayload(crawler.provider, crawler.content_type));
    },
    [runMutation, session.environment],
  );
  const crawlerColumns = useMemo<ColumnDef<CrawlerSummary>[]>(
    () => [
      {
        accessorKey: 'provider',
        header: 'Provider · 실행',
        cell: ({ row }) => {
          const isRunning = ['queued', 'running', 'stopping'].includes(row.original.status);
          const isSubmitting = runMutation.isPending
            && runMutation.variables?.provider === row.original.provider;
          if (session.role === 'viewer') return row.original.provider;
          const isDisabled = !row.original.can_run || isRunning || runMutation.isPending;
          const title = !row.original.can_run
            ? row.original.run_blocked_reason || '실행할 수 없는 Provider입니다.'
            : isRunning
              ? '이미 실행 중입니다.'
              : `${row.original.provider} 크롤러 실행`;
          return (
            <button
              className="table-provider-button"
              type="button"
              disabled={isDisabled}
              title={title}
              onClick={(event) => {
                event.stopPropagation();
                queueProviderRun(row.original);
              }}
            >
              <span>{row.original.provider}</span>
              <small>
                {!row.original.can_run ? '실행 불가' : isRunning ? '실행 중' : isSubmitting ? '등록 중…' : '실행'}
              </small>
            </button>
          );
        },
      },
      { accessorKey: 'content_type', header: '유형' },
      { accessorKey: 'status', header: '현재 상태', cell: ({ row }) => <StatusBadge status={row.original.status} /> },
      { accessorKey: 'last_run_status', header: '최근 결과', cell: ({ row }) => <StatusBadge status={row.original.last_run_status} /> },
      {
        accessorKey: 'last_run_trigger',
        header: '최근 방식',
        cell: ({ row }) => (
          <span className={`run-trigger ${row.original.last_run_trigger === 'local_schedule' ? 'automatic' : ''}`}>
            {runTriggerLabel(row.original.last_run_trigger)}
          </span>
        ),
      },
      { accessorKey: 'last_run_at', header: '마지막 실행', cell: ({ row }) => formatDate(row.original.last_run_at) },
      { accessorKey: 'active_course_count', header: '활성 데이터', cell: ({ row }) => formatNumber(row.original.active_course_count) },
      { accessorKey: 'consecutive_failures', header: '연속 실패', cell: ({ row }) => formatNumber(row.original.consecutive_failures) },
      {
        id: 'content',
        header: '수집 내용',
        enableSorting: false,
        cell: ({ row }) => (
          <button
            className="table-text-button"
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              const params = new URLSearchParams({ provider: row.original.provider, state: 'active' });
              navigate(`/content?${params.toString()}`);
            }}
          >
            데이터 보기
          </button>
        ),
      },
    ],
    [navigate, queueProviderRun, runMutation.isPending, runMutation.variables, session.role],
  );
  const runColumns = useMemo<ColumnDef<CrawlerRun>[]>(
    () => [
      { accessorKey: 'crawler_name', header: '크롤러' },
      { accessorKey: 'provider', header: 'Provider', cell: ({ row }) => row.original.provider || '-' },
      { accessorKey: 'content_type', header: '유형' },
      {
        accessorKey: 'trigger',
        header: '실행 방식',
        cell: ({ row }) => (
          <span className={`run-trigger ${row.original.trigger === 'local_schedule' ? 'automatic' : ''}`}>
            {runTriggerLabel(row.original.trigger)}
          </span>
        ),
      },
      { accessorKey: 'status', header: '결과', cell: ({ row }) => <StatusBadge status={row.original.status} /> },
      { accessorKey: 'total_count', header: '수집', cell: ({ row }) => formatNumber(row.original.total_count) },
      { accessorKey: 'new_count', header: '신규', cell: ({ row }) => formatNumber(row.original.new_count) },
      { accessorKey: 'updated_count', header: '변경', cell: ({ row }) => formatNumber(row.original.updated_count) },
      { accessorKey: 'started_at', header: '시작', cell: ({ row }) => formatDate(row.original.started_at) },
    ],
    [],
  );
  const crawlerItems = (crawlers.data?.items || []).filter(
    (item) => !requestedProvider || item.provider === requestedProvider,
  );
  const runItems = (runs.data?.items || []).filter(
    (item) => !requestedProvider || item.provider === requestedProvider,
  );

  return (
    <>
      <PageHeader
        eyebrow="COLLECTION CONTROL"
        title="Crawlers"
        description="Provider별 현재 상태, 실행 이력, 실패 근거와 대기열 작업을 한 흐름으로 확인합니다."
        actions={
          <>
            <Link className="button subtle" to="/crawler-improvements">
              개선 큐
            </Link>
            <Link className="button subtle" to="/crawlers/region-coverage">
              지역별 수집 현황
            </Link>
            {session.role !== 'viewer' ? (
              <>
                <button className="button subtle" type="button" onClick={() => setShowProbe(true)}>
                  Parser Probe
                </button>
                <button className="button primary" type="button" onClick={() => setShowRun(true)}>
                  크롤러 실행
                </button>
              </>
            ) : null}
          </>
        }
      />
      {(runMutation.error || probeMutation.error) && <QueryState error={runMutation.error || probeMutation.error} />}
      <section className="panel">
        <header className="section-header">
          <h2>{requestedProvider ? `${requestedProvider} 크롤러` : '크롤러 목록'}</h2>
        </header>
        <QueryState loading={crawlers.isLoading} error={crawlers.error} unavailable={crawlers.data?.available === false} empty={crawlers.data?.available === true && crawlerItems.length === 0} />
        {crawlerItems.length ? (
          <DataTable data={crawlerItems} columns={crawlerColumns} exportName="mooncen-crawlers.csv" />
        ) : null}
      </section>
      <section className="panel">
        <header className="section-header">
          <div>
            <h2>실행 이력</h2>
            <small>자동 스케줄과 수동 실행을 구분해 표시합니다.</small>
          </div>
        </header>
        <QueryState loading={runs.isLoading} error={runs.error} unavailable={runs.data?.available === false} empty={runs.data?.available === true && runItems.length === 0} />
        {runItems.length ? (
          <DataTable data={runItems} columns={runColumns} exportName="mooncen-crawler-runs.csv" onRowClick={(row) => navigate(`/crawlers/runs/${row.id}`)} />
        ) : null}
      </section>

      {showRun && (
        <DetailPanel title="크롤러 실행 등록" onClose={() => setShowRun(false)}>
          <form className="stack-form" onSubmit={submitRun}>
            <label>
              Provider 코드
              <input value={provider} onChange={(event) => setProvider(event.target.value)} required maxLength={100} placeholder="예: SUWON_LIBRARY" />
            </label>
            <label>
              데이터 유형
              <select value={contentType} onChange={(event) => setContentType(event.target.value)}>
                <option value="all">전체 / Provider 기준</option>
                <option value="culture_center">문화센터</option>
                <option value="experience">체험</option>
                <option value="education">교육</option>
              </select>
            </label>
            <p className="form-note">작업은 즉시 성공 처리되지 않습니다. PostgreSQL 대기열 등록 후 Agent 처리 결과가 Job에 기록됩니다.</p>
            <button className="button primary" type="submit" disabled={runMutation.isPending}>
              {runMutation.isPending ? '등록 중…' : '대기열에 등록'}
            </button>
          </form>
        </DetailPanel>
      )}
      {showProbe && (
        <DetailPanel title="Parser Probe" onClose={() => setShowProbe(false)}>
          <form
            className="stack-form"
            onSubmit={(event) => {
              event.preventDefault();
              if (probeUrl.trim()) probeMutation.mutate();
            }}
          >
            <label>
              점검할 공개 URL
              <input
                type="url"
                value={probeUrl}
                onChange={(event) => setProbeUrl(event.target.value)}
                required
                maxLength={4096}
                placeholder="https://..."
              />
            </label>
            <p className="form-note">DB에 저장하지 않고 정적 HTML, selector 근거, 추출 필드, 필수 필드와 신청 URL 후보를 검사합니다.</p>
            <button className="button primary" type="submit" disabled={probeMutation.isPending}>
              {probeMutation.isPending ? '등록 중…' : '진단 Job 등록'}
            </button>
          </form>
        </DetailPanel>
      )}
      {id && (
        <DetailPanel title="크롤러 실행 상세" onClose={() => navigate('/crawlers')}>
          <QueryState loading={detail.isLoading} error={detail.error} />
          {detail.data && <DefinitionList value={detail.data} />}
          <h3>작업 로그 · 최근 1,000건</h3>
          {runJobId ? (
            <>
              <QueryState loading={runLogs.isLoading} error={runLogs.error} empty={runLogs.data?.items.length === 0} />
              {runLogs.data?.items.length ? (
                <div className="log-viewer">
                  {runLogs.data.items.map((log) => (
                    <div key={String(log.id)}>
                      <time>{formatDate(log.created_at)}</time>
                      <strong>{String(log.log_level || 'info')}</strong>
                      <span>{String(log.message || '')}</span>
                    </div>
                  ))}
                </div>
              ) : null}
            </>
          ) : (
            <p className="form-note">Ops 작업과 연결되지 않은 직접 실행 기록입니다.</p>
          )}
          <h3>실패 근거</h3>
          <QueryState loading={errors.isLoading} error={errors.error} empty={errors.data?.items.length === 0} />
          {errors.data?.items.map((error) => (
            <article className="error-card" key={String(error.id)}>
              <StatusBadge status="failed" />
              <strong>{String(error.error_type || 'unknown_error')}</strong>
              <p>{String(error.message || '-')}</p>
              {error.source_url ? (
                <a href={String(error.source_url)} target="_blank" rel="noreferrer">
                  원본 페이지 열기
                </a>
              ) : null}
            </article>
          ))}
        </DetailPanel>
      )}
    </>
  );
}
