import { useQuery } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { useMemo } from 'react';
import { useNavigate, useParams } from 'react-router';
import { opsApi } from '../api';
import DataTable from '../components/DataTable';
import StatusBadge from '../components/StatusBadge';
import { DefinitionList, DetailPanel, PageHeader, QueryState } from '../components/Ui';
import { useUrlFilters } from '../hooks/useUrlFilters';
import type { OpsService, PageResponse } from '../types';
import { formatDate } from '../utils';

const serviceFilterDefaults = { service_type: '' };

export default function ServicesPage() {
  const navigate = useNavigate();
  const { id } = useParams();
  const [filters, updateFilters] = useUrlFilters('services', serviceFilterDefaults);
  const type = filters.service_type;
  const query = useQuery({
    queryKey: ['services', type],
    queryFn: () => opsApi<PageResponse<OpsService>>(`/services${type ? `?service_type=${encodeURIComponent(type)}` : ''}`),
    refetchInterval: 30_000,
  });
  const detail = useQuery({
    queryKey: ['service', id],
    queryFn: () => opsApi<OpsService>(`/services/${id}`),
    enabled: Boolean(id),
  });
  const columns = useMemo<ColumnDef<OpsService>[]>(
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
      { accessorKey: 'environment', header: '환경' },
      { accessorKey: 'status', header: '상태', cell: ({ row }) => <StatusBadge status={row.original.status} /> },
      {
        accessorKey: 'response_time_ms',
        header: '응답',
        cell: ({ row }) => (row.original.response_time_ms == null ? '-' : `${String(row.original.response_time_ms)}ms`),
      },
      { accessorKey: 'current_version', header: '버전', cell: ({ row }) => String(row.original.current_version || '-') },
      { accessorKey: 'last_checked_at', header: '마지막 확인', cell: ({ row }) => formatDate(row.original.last_checked_at) },
    ],
    [],
  );
  return (
    <>
      <PageHeader
        eyebrow="RUNTIME INVENTORY"
        title="Services"
        description="설정 소유자와 상태 보고 출처를 분리해 표시합니다. Endpoint와 보고 Agent는 실행 호스트 증거가 아닙니다."
        actions={
          <button className="button subtle" type="button" onClick={() => navigate('/agents')}>
            Agent 목록
          </button>
        }
      />
      <div className="filter-row">
        <label>
          서비스 종류
          <select value={type} onChange={(event) => updateFilters({ service_type: event.target.value })}>
            <option value="">전체</option>
            {['frontend', 'backend', 'database', 'staging_database', 'crawler', 'crawler_control', 'ai_worker', 'agent', 'proxy', 'scheduler'].map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </label>
      </div>
      <QueryState loading={query.isLoading} error={query.error} unavailable={query.data?.available === false} empty={query.data?.items.length === 0} />
      {query.data?.items.length ? (
        <DataTable data={query.data.items} columns={columns} exportName="mooncen-services.csv" onRowClick={(row) => navigate(`/services/${row.id}`)} />
      ) : null}
      {id && (
        <DetailPanel title="서비스 상세" onClose={() => navigate('/services')}>
          <QueryState loading={detail.isLoading} error={detail.error} />
          {detail.data && <DefinitionList value={detail.data} />}
        </DetailPanel>
      )}
    </>
  );
}
