import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { useMemo, useState } from 'react';
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

type CrawlerOwnerStatus = {
  available: boolean;
  owner: string;
  host?: string;
  reason?: string;
  timer?: Record<string, unknown>;
  run?: Record<string, unknown>;
  dispatch: {
    running: boolean;
    started_at?: string | null;
    finished_at?: string | null;
    exit_code?: number | null;
    error?: string | null;
  };
};

function runTriggerLabel(trigger: unknown): string {
  if (trigger === 'local_schedule') return '자동';
  if (trigger === 'standalone') return '직접 실행';
  if (trigger === 'manual') return '수동';
  return '확인 불가';
}

function confirmProductionRunAll(): boolean {
  return window.prompt('운영 크롤러 전체 실행 확인을 위해 MOONCEN-CRAWLER-ALL을 입력하세요.') === 'MOONCEN-CRAWLER-ALL';
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
  const [showProbe, setShowProbe] = useState(false);
  const [probeUrl, setProbeUrl] = useState('');
  const crawlers = useQuery({
    queryKey: ['crawlers'],
    queryFn: () => opsApi<{ available: boolean; items: CrawlerSummary[]; total: number }>('/crawlers'),
    refetchInterval: 30_000,
  });
  const ownerStatus = useQuery({
    queryKey: ['crawler-owner-status'],
    queryFn: () => opsApi<CrawlerOwnerStatus>('/crawlers/owner/status'),
    refetchInterval: 15_000,
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
  const runAllMutation = useMutation({
    mutationFn: () => opsApi<{ accepted: boolean; owner: string }>('/crawlers/owner/run-all', {
      method: 'POST',
      body: JSON.stringify({ confirmation: 'MOONCEN-CRAWLER-ALL' }),
    }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['crawler-owner-status'] });
      void queryClient.invalidateQueries({ queryKey: ['crawler-runs'] });
    },
  });
  const crawlerColumns = useMemo<ColumnDef<CrawlerSummary>[]>(
    () => [
      {
        accessorKey: 'provider',
        header: 'Provider',
        cell: ({ row }) => row.original.provider,
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
    [navigate],
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
                <button
                  className="button primary"
                  type="button"
                  disabled={
                    ownerStatus.isLoading
                    || ownerStatus.data?.available !== true
                    || ownerStatus.data?.dispatch.running === true
                    || ['active', 'activating', 'reloading'].includes(String(ownerStatus.data?.run?.ActiveState || ''))
                    || runAllMutation.isPending
                  }
                  title={ownerStatus.data?.available === false ? ownerStatus.data.reason : 'gen1crawler에서 모든 Provider를 1회 실행합니다.'}
                  onClick={() => {
                    if (confirmProductionRunAll()) runAllMutation.mutate();
                  }}
                >
                  {runAllMutation.isPending || ownerStatus.data?.dispatch.running ? '전체 실행 요청 중…' : '전체 크롤러 실행'}
                </button>
                <button className="button subtle" type="button" onClick={() => setShowProbe(true)}>
                  Parser Probe
                </button>
              </>
            ) : null}
          </>
        }
      />
      {(probeMutation.error || runAllMutation.error) && <QueryState error={probeMutation.error || runAllMutation.error} />}
      <section className="panel">
        <header className="section-header">
          <div>
            <h2>운영 전체 실행</h2>
            <small>고정된 gen1crawler 운영 helper를 통해 모든 Provider를 1회 실행합니다.</small>
          </div>
        </header>
        <QueryState loading={ownerStatus.isLoading} error={ownerStatus.error} />
        {ownerStatus.data && (
          <DefinitionList
            value={{
              available: ownerStatus.data.available,
              owner: ownerStatus.data.owner,
              timer_state: ownerStatus.data.timer?.ActiveState || 'unknown',
              timer_enabled: ownerStatus.data.timer?.UnitFileState || 'unknown',
              run_state: ownerStatus.data.run?.ActiveState || 'unknown',
              run_result: ownerStatus.data.run?.Result || 'unknown',
              dispatch_running: ownerStatus.data.dispatch.running,
              dispatch_error: ownerStatus.data.dispatch.error || ownerStatus.data.reason || null,
            }}
          />
        )}
      </section>
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
