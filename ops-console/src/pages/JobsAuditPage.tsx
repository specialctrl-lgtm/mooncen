import { useMemo } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { NavLink, useNavigate, useParams } from 'react-router';
import { opsApi } from '../api';
import DataTable from '../components/DataTable';
import StatusBadge from '../components/StatusBadge';
import { DefinitionList, DetailPanel, PageHeader, QueryState, StreamStatus } from '../components/Ui';
import { useOpsSession } from '../context';
import { appendStreamLog, useJobEventStream } from '../hooks/useJobEventStream';
import type { OpsJob, PageResponse } from '../types';
import { formatDate } from '../utils';

type AuditRow = Record<string, unknown> & { id: number; action: string; resource_type: string; result: string };

export default function JobsAuditPage({ view = 'jobs' }: { view?: 'jobs' | 'audit' }) {
  const { id } = useParams();
  const navigate = useNavigate();
  const session = useOpsSession();
  const queryClient = useQueryClient();
  const jobs = useQuery({
    queryKey: ['jobs'],
    queryFn: () => opsApi<PageResponse<OpsJob>>('/jobs?limit=100'),
    refetchInterval: 15_000,
    enabled: view === 'jobs',
  });
  const audit = useQuery({
    queryKey: ['audit'],
    queryFn: () => opsApi<PageResponse<AuditRow>>('/audit-logs?limit=100'),
    enabled: view === 'audit',
  });
  const jobDetail = useQuery({
    queryKey: ['job', id],
    queryFn: () => opsApi<Record<string, unknown>>(`/jobs/${id}`),
    enabled: view === 'jobs' && Boolean(id),
  });
  const jobLogs = useQuery({
    queryKey: ['job-logs', id],
    queryFn: () => opsApi<{ available: boolean; items: Array<Record<string, unknown>> }>(`/jobs/${id}/logs?limit=1000`),
    enabled: view === 'jobs' && Boolean(id),
  });
  const auditDetail = useQuery({
    queryKey: ['audit-row', id],
    queryFn: () => opsApi<Record<string, unknown>>(`/audit-logs/${id}`),
    enabled: view === 'audit' && Boolean(id),
  });
  const cancel = useMutation({
    mutationFn: () =>
      opsApi(`/jobs/${id}/cancel`, {
        method: 'POST',
        body: JSON.stringify({ reason: '운영 콘솔에서 취소 요청' }),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['job', id] });
      void queryClient.invalidateQueries({ queryKey: ['jobs'] });
    },
  });

  const jobId = id || '';
  const streamActive = Boolean(
    view === 'jobs'
    && jobId
    && ['queued', 'assigned', 'running'].includes(String(jobDetail.data?.status)),
  );
  const stream = useJobEventStream({
    jobId,
    enabled: streamActive,
    onJob: (job) => {
      queryClient.setQueryData<Record<string, unknown>>(['job', id], (currentJob) => (
        currentJob ? { ...currentJob, ...job } : currentJob
      ));
      queryClient.setQueryData<PageResponse<OpsJob>>(['jobs'], (currentPage) => (
        currentPage
          ? {
              ...currentPage,
              items: currentPage.items.map((item) => (
                item.id === jobId ? { ...item, ...job } as OpsJob : item
              )),
            }
          : currentPage
      ));
    },
    onLog: (log) => {
      queryClient.setQueryData<{ available: boolean; items: Array<Record<string, unknown>> }>(
        ['job-logs', id],
        (currentLogs) => appendStreamLog(currentLogs, log),
      );
    },
    onEnd: () => {
      void queryClient.invalidateQueries({ queryKey: ['job', id] });
      void queryClient.invalidateQueries({ queryKey: ['jobs'] });
    },
  });

  const jobColumns = useMemo<ColumnDef<OpsJob>[]>(
    () => [
      { accessorKey: 'job_type', header: '작업 종류' },
      { accessorKey: 'target_key', header: '대상', cell: ({ row }) => row.original.target_key || '-' },
      { accessorKey: 'status', header: '상태', cell: ({ row }) => <StatusBadge status={row.original.status} /> },
      { accessorKey: 'progress', header: '진행률', cell: ({ row }) => `${row.original.progress}%` },
      { accessorKey: 'queued_at', header: '등록', cell: ({ row }) => formatDate(row.original.queued_at) },
      { accessorKey: 'started_at', header: '시작', cell: ({ row }) => formatDate(row.original.started_at) },
      { accessorKey: 'finished_at', header: '종료', cell: ({ row }) => formatDate(row.original.finished_at) },
    ],
    [],
  );
  const auditColumns = useMemo<ColumnDef<AuditRow>[]>(
    () => [
      { accessorKey: 'user_email', header: '사용자', cell: ({ row }) => String(row.original.user_email || '-') },
      { accessorKey: 'action', header: '작업' },
      { accessorKey: 'resource_type', header: '대상 유형' },
      { accessorKey: 'resource_id', header: '대상', cell: ({ row }) => String(row.original.resource_id || '-') },
      { accessorKey: 'result', header: '결과', cell: ({ row }) => <StatusBadge status={row.original.result} /> },
      { accessorKey: 'created_at', header: '시간', cell: ({ row }) => formatDate(row.original.created_at) },
    ],
    [],
  );

  return (
    <>
      <PageHeader eyebrow="TRACEABLE OPERATIONS" title="Jobs & Audit" description="비동기 작업의 진행 상태와 누가 언제 실행한 조작인지 함께 추적합니다." />
      <div className="segmented">
        <NavLink to="/jobs">Jobs</NavLink>
        <NavLink to="/audit">Audit Log</NavLink>
      </div>
      {view === 'jobs' ? (
        <>
          <QueryState loading={jobs.isLoading} error={jobs.error} unavailable={jobs.data?.available === false} empty={jobs.data?.items.length === 0} />
          {jobs.data?.items.length ? (
            <DataTable data={jobs.data.items} columns={jobColumns} exportName="mooncen-jobs.csv" onRowClick={(row) => navigate(`/jobs/${row.id}`)} />
          ) : null}
        </>
      ) : (
        <>
          <QueryState loading={audit.isLoading} error={audit.error} unavailable={audit.data?.available === false} empty={audit.data?.items.length === 0} />
          {audit.data?.items.length ? (
            <DataTable data={audit.data.items} columns={auditColumns} exportName="mooncen-audit.csv" onRowClick={(row) => navigate(`/audit/${row.id}`)} />
          ) : null}
        </>
      )}
      {view === 'jobs' && id && (
        <DetailPanel title="Job 상세" onClose={() => navigate('/jobs')}>
          <QueryState loading={jobDetail.isLoading} error={jobDetail.error || cancel.error} />
          {jobDetail.data && (
            <>
              <DefinitionList value={jobDetail.data} />
              {session.role !== 'viewer' && ['queued', 'assigned', 'running'].includes(String(jobDetail.data.status)) ? (
                <button
                  className="button danger"
                  type="button"
                  disabled={cancel.isPending}
                  onClick={() => {
                    const confirmed =
                      session.environment === 'production'
                        ? window.prompt('운영 작업 취소 확인을 위해 MOONCEN-PRODUCTION을 입력하세요.') === 'MOONCEN-PRODUCTION'
                        : window.confirm('이 작업에 취소를 요청할까요?');
                    if (confirmed) cancel.mutate();
                  }}
                >
                  작업 취소 요청
                </button>
              ) : null}
            </>
          )}
          <h3>실시간 작업 로그</h3>
          <StreamStatus state={stream.state} detail={stream.detail} />
          <QueryState loading={jobLogs.isLoading} error={jobLogs.error} empty={jobLogs.data?.items.length === 0} />
          {jobLogs.data?.items.length ? (
            <div className="log-viewer">
              {jobLogs.data.items.map((log) => (
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
      {view === 'audit' && id && (
        <DetailPanel title="감사 로그 상세" onClose={() => navigate('/audit')}>
          <QueryState loading={auditDetail.isLoading} error={auditDetail.error} />
          {auditDetail.data && <DefinitionList value={auditDetail.data} />}
        </DetailPanel>
      )}
    </>
  );
}
