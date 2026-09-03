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

type DeployTarget = {
  name: string;
  server: string;
  domain: string;
  remote_dir: string;
  role: 'primary' | 'standby' | 'crawler' | 'crawler-control';
  deploy_profile?: 'full-stack' | 'crawler-only' | 'control-only';
  active: boolean;
  key_ready: boolean;
  services: Array<{ service: string; role: string; replicates_from?: string | null }>;
};

type DeployReadiness = {
  available: boolean;
  can_deploy: boolean;
  default_target: string | null;
  targets: DeployTarget[];
  snapshot: {
    branch: string;
    commit: string;
    short_commit: string;
    clean: boolean;
    changed_count: number;
    source_tree: string;
    short_source_tree: string;
    deploy_path_count: number;
    excluded_count: number;
  } | null;
  agent: { hostname: string } | null;
  reasons: Array<{ code: string; message: string }>;
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

export default function DeploymentsPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const session = useOpsSession();
  const queryClient = useQueryClient();
  const [showDeploy, setShowDeploy] = useState(false);
  const [selectedTarget, setSelectedTarget] = useState('');
  const [skipWorkers, setSkipWorkers] = useState(false);
  const [confirmation, setConfirmation] = useState('');

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
  const detail = useQuery({
    queryKey: ['deployment', id],
    queryFn: () => opsApi<Record<string, unknown>>(`/deployments/${id}`),
    enabled: Boolean(id),
    refetchInterval: id ? 5_000 : false,
  });
  const jobId = String(detail.data?.job_id || '');
  const logs = useQuery({
    queryKey: ['deployment-logs', jobId],
    queryFn: () => opsApi<{ available: boolean; items: Array<Record<string, unknown>> }>(
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

  const stream = useJobEventStream({
    jobId,
    enabled: Boolean(jobId && ['queued', 'assigned', 'running'].includes(String(detail.data?.job_status))),
    onJob: (job) => {
      queryClient.setQueryData<Record<string, unknown>>(['deployment', id], (value) => (
        value ? { ...value, job_status: job.status, progress: job.progress, error_code: job.error_code,
          error_message: job.error_message, finished_at: job.finished_at } : value
      ));
    },
    onLog: (log) => {
      queryClient.setQueryData<{ available: boolean; items: Array<Record<string, unknown>> }>(
        ['deployment-logs', jobId],
        (value) => appendStreamLog(value, log),
      );
    },
    onEnd: () => {
      void queryClient.invalidateQueries({ queryKey: ['deployment', id] });
      void queryClient.invalidateQueries({ queryKey: ['deployments'] });
    },
  });

  const selected = readiness.data?.targets.find((target) => target.name === selectedTarget);
  const snapshot = readiness.data?.snapshot;
  const confirmationText = selected && snapshot ? `DEPLOY ${selected.name} ${snapshot.short_source_tree}` : '';
  const canCreate = Boolean(
    session.role === 'admin' && readiness.data?.can_deploy && selected?.key_ready && snapshot?.source_tree,
  );

  const createDeployment = useMutation({
    mutationFn: () => opsApi<{ deployment: Deployment }>('/deployments', {
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
  const cancelDeployment = useMutation({
    mutationFn: () => opsApi(`/jobs/${jobId}/cancel`, {
      method: 'POST',
      body: JSON.stringify({ reason: 'Deployments 화면에서 배포 취소 요청' }),
    }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['deployment', id] }),
  });

  const columns = useMemo<ColumnDef<Deployment>[]>(() => [
    { accessorKey: 'target', header: '대상', cell: ({ row }) => String(row.original.target || '-') },
    { accessorKey: 'service_type', header: '범위' },
    { accessorKey: 'target_version', header: '버전', cell: ({ row }) => String(row.original.target_version || '-') },
    { accessorKey: 'target_commit', header: 'Commit', cell: ({ row }) => String(row.original.target_commit || '-').slice(0, 12) },
    { accessorKey: 'deployment_status', header: '상태', cell: ({ row }) => <StatusBadge status={row.original.deployment_status} /> },
    { accessorKey: 'progress', header: '진행률', cell: ({ row }) => `${Number(row.original.progress || 0)}%` },
    { accessorKey: 'started_at', header: '시작', cell: ({ row }) => formatDate(row.original.started_at) },
    { accessorKey: 'finished_at', header: '종료', cell: ({ row }) => formatDate(row.original.finished_at) },
  ], []);
  const currentColumns = useMemo<ColumnDef<OpsService>[]>(() => [
    { accessorKey: 'service_name', header: '서비스' },
    { accessorKey: 'service_type', header: '종류' },
    { accessorKey: 'service_host', header: '상태 확인 Endpoint', cell: ({ row }) => String(row.original.service_host || '-') },
    { accessorKey: 'reporter_hostname', header: '상태 보고 Agent', cell: ({ row }) => String(row.original.reporter_hostname || '-') },
    { accessorKey: 'current_version', header: '버전', cell: ({ row }) => String(row.original.current_version || '-') },
    { accessorKey: 'current_commit', header: 'Commit', cell: ({ row }) => String(row.original.current_commit || '-').slice(0, 12) },
    { accessorKey: 'status', header: '상태', cell: ({ row }) => <StatusBadge status={row.original.status} /> },
    { accessorKey: 'last_checked_at', header: '확인', cell: ({ row }) => formatDate(row.original.last_checked_at) },
  ], []);

  const refreshAll = () => {
    void queryClient.invalidateQueries({ queryKey: ['deployment-readiness'] });
    void queryClient.invalidateQueries({ queryKey: ['deployment-services'] });
    void queryClient.invalidateQueries({ queryKey: ['deployments'] });
  };

  return (
    <>
      <PageHeader
        eyebrow="NATIVE RELEASE"
        title="Deployments"
        description="검토된 개발 스냅샷을 운영 서버의 native 서비스로 배포합니다."
        actions={<>
          <button className="icon-button" type="button" onClick={refreshAll} title="배포 상태 새로고침" aria-label="배포 상태 새로고침">
            <RefreshCw size={17} aria-hidden="true" />
          </button>
          {session.role === 'admin' ? (
            <button className="button primary button-with-icon" type="button" disabled={!canCreate}
              onClick={() => { setSelectedTarget(readiness.data?.default_target || readiness.data?.targets[0]?.name || ''); setConfirmation(''); setShowDeploy(true); }}>
              <CloudUpload size={17} aria-hidden="true" /> 네이티브 배포
            </button>
          ) : null}
        </>}
      />

      <section className="panel">
        <header className="section-header"><div><h2>네이티브 배포 스냅샷</h2><small>Docker 승격 없이 native 서비스를 직접 갱신합니다.</small></div></header>
        <QueryState loading={readiness.isLoading} error={readiness.error} unavailable={readiness.data?.available === false} />
        {snapshot ? <div className="deploy-source-grid">
          <div><GitBranch size={18} /><span>브랜치</span><strong>{snapshot.branch}</strong></div>
          <div><ShieldCheck size={18} /><span>Commit</span><strong className="mono-value">{snapshot.short_commit}</strong></div>
          <div><ShieldCheck size={18} /><span>Source tree</span><strong className="mono-value">{snapshot.short_source_tree}</strong></div>
          <div><Server size={18} /><span>배포 Agent</span><strong>{readiness.data?.agent?.hostname || '연결 안 됨'}</strong></div>
          <div><CloudUpload size={18} /><span>작업 트리</span><strong>{snapshot.clean ? 'HEAD와 동일' : `변경 ${snapshot.changed_count}개 포함`}</strong></div>
          <div><Server size={18} /><span>배포 경로</span><strong>{snapshot.deploy_path_count}개</strong></div>
        </div> : null}
        {readiness.data?.reasons.length ? <div className="deploy-blockers" role="status">
          {readiness.data.reasons.map((reason) => <span key={reason.code}>{reason.message}</span>)}
        </div> : null}
      </section>

      <section className="panel">
        <header className="section-header"><h2>배포 대상</h2></header>
        <div className="deploy-target-grid">{readiness.data?.targets.map((target) => (
          <article key={target.name}><div><Server size={18} /><strong>{target.name}</strong><StatusBadge status={target.active ? 'healthy' : 'idle'} /></div>
            <dl><div><dt>역할</dt><dd>{target.role}</dd></div><div><dt>호스트</dt><dd>{target.server}</dd></div>
              <div><dt>운영 서비스</dt><dd>{targetServiceSummary(target)}</dd></div><div><dt>도메인</dt><dd>{target.domain}</dd></div>
              <div><dt>배포 키</dt><dd>{target.key_ready ? '준비됨' : '확인 필요'}</dd></div></dl>
          </article>
        ))}</div>
      </section>

      <section className="panel"><header className="section-header"><h2>현재 실행 버전</h2></header>
        <QueryState loading={current.isLoading} error={current.error} unavailable={current.data?.available === false} empty={current.data?.items.length === 0} />
        {current.data?.items.length ? <DataTable data={current.data.items} columns={currentColumns} exportName="mooncen-current-services.csv" /> : null}
      </section>

      <section className="panel"><header className="section-header"><h2>배포 작업 이력</h2></header>
        <QueryState loading={deployments.isLoading} error={deployments.error} unavailable={deployments.data?.available === false} empty={deployments.data?.items.length === 0} />
        {deployments.data?.items.length ? <DataTable data={deployments.data.items} columns={columns} exportName="mooncen-deployments.csv" onRowClick={(row) => navigate(`/deployments/${row.id}`)} /> : null}
      </section>

      {showDeploy ? <DetailPanel title="네이티브 배포" onClose={() => setShowDeploy(false)}>
        <form className="stack-form" onSubmit={(event) => { event.preventDefault(); if (confirmation === confirmationText) createDeployment.mutate(); }}>
          <label>배포 대상<select value={selectedTarget} onChange={(event) => { setSelectedTarget(event.target.value); setConfirmation(''); }}>
            {readiness.data?.targets.map((target) => <option key={target.name} value={target.name} disabled={!target.key_ready}>{target.name} · {target.role} · {target.server}</option>)}
          </select></label>
          <label>개발 스냅샷<input className="mono-value" value={snapshot?.source_tree || ''} readOnly /></label>
          <label className="check-row"><input type="checkbox" checked={skipWorkers} onChange={(event) => setSkipWorkers(event.target.checked)} />크롤러·AI worker 갱신 제외</label>
          <label>확인 문구<code className="confirmation-code">{confirmationText}</code><input aria-label="확인 문구" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} autoComplete="off" required /></label>
          <QueryState error={createDeployment.error} />
          <button className="button primary button-with-icon" type="submit" disabled={!canCreate || confirmation !== confirmationText || createDeployment.isPending}>
            <CloudUpload size={17} /> {createDeployment.isPending ? '등록 중…' : `${selectedTarget} 배포 시작`}
          </button>
        </form>
      </DetailPanel> : null}

      {id ? <DetailPanel title="배포 상세" onClose={() => navigate('/deployments')}>
        <QueryState loading={detail.isLoading} error={detail.error || cancelDeployment.error} />
        {detail.data ? <><DefinitionList value={detail.data} />
          {session.role === 'admin' && ['queued', 'assigned', 'running'].includes(String(detail.data.job_status)) ? (
            <button className="button danger" type="button" disabled={cancelDeployment.isPending} onClick={() => {
              const expected = `CANCEL ${String(detail.data?.target || '')}`;
              if (window.prompt(`배포 취소 확인을 위해 ${expected}을 입력하세요.`) === expected) cancelDeployment.mutate();
            }}>배포 취소 요청</button>
          ) : null}</> : null}
        <h3>실시간 배포 로그</h3><StreamStatus state={stream.state} detail={stream.detail} />
        <QueryState loading={logs.isLoading} error={logs.error} empty={logs.data?.items.length === 0} />
        {logs.data?.items.length ? <div className="log-viewer">{logs.data.items.map((log) => <div key={String(log.id)}>
          <time>{formatDate(log.created_at)}</time><strong>{String(log.log_level || 'info')}</strong><span>{String(log.message || '')}</span>
        </div>)}</div> : null}
      </DetailPanel> : null}
    </>
  );
}
