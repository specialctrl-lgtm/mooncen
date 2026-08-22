import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { useNavigate, useParams } from 'react-router';
import { opsApi } from '../api';
import DataTable from '../components/DataTable';
import StatusBadge from '../components/StatusBadge';
import { DefinitionList, DetailPanel, PageHeader, QueryState } from '../components/Ui';
import { useUrlFilters } from '../hooks/useUrlFilters';
import type { PageResponse } from '../types';
import { formatDate, formatNumber } from '../utils';

type ContentItem = Record<string, unknown> & {
  id: string;
  provider: string;
  content_type: string;
  category: string;
  category_encoding_issue?: boolean;
  title: string;
  is_active: boolean;
};

const defaults = { content_type: '', category: '', provider: '', query: '', state: 'active' };

export default function ContentPage() {
  const navigate = useNavigate();
  const { id } = useParams();
  const [filters, updateFilters] = useUrlFilters('content', defaults);
  const params = new URLSearchParams({ limit: '100', state: filters.state });
  if (filters.content_type) params.set('content_type', filters.content_type);
  if (filters.category) params.set('category', filters.category);
  if (filters.provider) params.set('provider', filters.provider);
  if (filters.query) params.set('query', filters.query);
  const list = useQuery({
    queryKey: ['content', filters],
    queryFn: () => opsApi<PageResponse<ContentItem>>(`/content?${params.toString()}`),
  });
  const detail = useQuery({
    queryKey: ['content-detail', id],
    queryFn: () => opsApi<Record<string, unknown>>(`/content/${id}`),
    enabled: Boolean(id),
  });
  const columns = useMemo<ColumnDef<ContentItem>[]>(
    () => [
      { accessorKey: 'content_type', header: '유형' },
      { accessorKey: 'category', header: '카테고리' },
      {
        accessorKey: 'category_encoding_issue',
        header: '원본 분류',
        cell: ({ row }) =>
          row.original.category_encoding_issue ? (
            <span className="quality-damage-count">인코딩 손상</span>
          ) : (
            '정상'
          ),
      },
      { accessorKey: 'provider', header: 'Provider' },
      { accessorKey: 'title', header: '제목' },
      { accessorKey: 'branch', header: '지점', cell: ({ row }) => String(row.original.branch || '-') },
      { accessorKey: 'status', header: '상태', cell: ({ row }) => <StatusBadge status={String(row.original.status || 'unknown')} /> },
      { accessorKey: 'quality_score', header: '품질', cell: ({ row }) => formatNumber(row.original.quality_score) },
      { accessorKey: 'last_seen_at', header: '최근 수집', cell: ({ row }) => formatDate(row.original.last_seen_at) },
      { accessorKey: 'is_active', header: '활성', cell: ({ row }) => (row.original.is_active ? '활성' : '종료') },
    ],
    [],
  );

  return (
    <>
      <PageHeader
        eyebrow="NORMALIZED CONTENT"
        title="Content"
        description="운영 DB의 문화센터·체험·교육 콘텐츠와 품질·원본 근거를 조회합니다."
      />
      <div className="filter-row">
        <label>
          유형
          <select value={filters.content_type} onChange={(event) => updateFilters({ content_type: event.target.value })}>
            <option value="">전체</option>
            <option value="culture_center">문화센터</option>
            <option value="experience">체험</option>
            <option value="education">교육</option>
          </select>
        </label>
        <label>
          카테고리
          <input value={filters.category} onChange={(event) => updateFilters({ category: event.target.value })} />
        </label>
        <label>
          Provider
          <input value={filters.provider} onChange={(event) => updateFilters({ provider: event.target.value.toUpperCase() })} />
        </label>
        <label>
          검색
          <input value={filters.query} onChange={(event) => updateFilters({ query: event.target.value })} />
        </label>
        <label>
          활성 상태
          <select value={filters.state} onChange={(event) => updateFilters({ state: event.target.value })}>
            <option value="active">활성</option>
            <option value="inactive">종료</option>
            <option value="all">전체</option>
          </select>
        </label>
      </div>
      <QueryState loading={list.isLoading} error={list.error} unavailable={list.data?.available === false} empty={list.data?.items.length === 0} />
      <section className="panel">
        <header className="section-header">
          <div>
            <h2>수집 콘텐츠</h2>
            <small>
              총 {formatNumber(list.data?.total)}건 중 최신 {formatNumber(list.data?.items.length)}건을 표시합니다.
            </small>
          </div>
        </header>
        {list.data?.items.length ? (
          <DataTable
            data={list.data.items}
            columns={columns}
            exportName="mooncen-content.csv"
            onRowClick={(row) => navigate(`/content/${row.content_type}/${row.id}?${params.toString()}`)}
          />
        ) : null}
      </section>
      {id && (
        <DetailPanel title="콘텐츠 상세" onClose={() => navigate(`/content?${params.toString()}`)}>
          <QueryState loading={detail.isLoading} error={detail.error} />
          {detail.data && (
            <>
              <DefinitionList value={detail.data} />
              <div className="button-row">
                {detail.data.raw_url ? (
                  <a className="button subtle" href={String(detail.data.raw_url)} target="_blank" rel="noreferrer">
                    원본 페이지
                  </a>
                ) : null}
                {detail.data.application_url ? (
                  <a className="button primary" href={String(detail.data.application_url)} target="_blank" rel="noreferrer">
                    신청 페이지
                  </a>
                ) : null}
              </div>
            </>
          )}
        </DetailPanel>
      )}
    </>
  );
}
