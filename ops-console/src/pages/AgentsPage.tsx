import { useQuery } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { useMemo } from 'react';
import { opsApi } from '../api';
import DataTable from '../components/DataTable';
import StatusBadge from '../components/StatusBadge';
import { PageHeader, QueryState } from '../components/Ui';
import { formatDate } from '../utils';

type Agent = Record<string, unknown> & { id: string; name: string; hostname: string; status: string };

export default function AgentsPage() {
  const query = useQuery({
    queryKey: ['agents'],
    queryFn: () => opsApi<{ available: boolean; items: Agent[] }>('/agents'),
    refetchInterval: 30_000,
  });
  const columns = useMemo<ColumnDef<Agent>[]>(
    () => [
      { accessorKey: 'name', header: 'Agent' },
      { accessorKey: 'hostname', header: '호스트' },
      { accessorKey: 'environment', header: '환경' },
      { accessorKey: 'os_type', header: 'OS' },
      { accessorKey: 'version', header: '버전', cell: ({ row }) => String(row.original.version || '-') },
      { accessorKey: 'status', header: '상태', cell: ({ row }) => <StatusBadge status={row.original.status} /> },
      { accessorKey: 'last_seen_at', header: '마지막 연결', cell: ({ row }) => formatDate(row.original.last_seen_at) },
      {
        accessorKey: 'maintenance_mode',
        header: '유지보수',
        cell: ({ row }) => (row.original.maintenance_mode ? '사용' : '해제'),
      },
    ],
    [],
  );
  return (
    <>
      <PageHeader eyebrow="SERVER WORKERS" title="Agents" description="등록된 Agent의 연결 상태와 보고된 기능을 조회합니다." />
      <QueryState loading={query.isLoading} error={query.error} unavailable={query.data?.available === false} empty={query.data?.items.length === 0} />
      {query.data?.items.length ? <DataTable data={query.data.items} columns={columns} exportName="mooncen-agents.csv" /> : null}
    </>
  );
}
