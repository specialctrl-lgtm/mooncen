import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { opsApi } from '../api';
import DataTable from '../components/DataTable';
import StatusBadge from '../components/StatusBadge';
import { DefinitionList, DetailPanel, PageHeader, QueryState, StatCard } from '../components/Ui';
import { useOpsSession } from '../context';
import type { PageResponse, QualitySummary } from '../types';
import { formatDate, formatNumber } from '../utils';

type ProviderQuality = Record<string, unknown> & {
  provider: string;
  content_type: string;
  active_count: number;
  average_score?: number | null;
  field_completeness: number;
  complete_count: number;
  target_count: number;
  fee_count: number;
  date_count: number;
  place_count: number;
  category_count: number;
  time_count: number;
  encoding_issue_count: number;
  bad_count: number;
  warning_count: number;
  unchecked_count: number;
  provider_urls?: string[];
};
type CategoryQuality = Record<string, unknown> & {
  content_type: string;
  category: string;
  active_count: number;
  provider_count: number;
  average_score?: number | null;
  field_completeness: number;
  complete_count: number;
  target_count: number;
  fee_count: number;
  date_count: number;
  place_count: number;
  category_count: number;
  time_count: number;
  encoding_issue_count: number;
  checked_count: number;
  good_count: number;
  bad_count: number;
  warning_count: number;
  unchecked_count: number;
};
type QualityIssue = Record<string, unknown> & {
  id: string;
  severity: string;
  issue_type: string;
  content_type: string;
  provider?: string | null;
  branch?: string | null;
  status: string;
};
type AddressFix = Record<string, unknown> & {
  id: string;
  provider: string;
  branch_code?: string | null;
  name: string;
  address?: string | null;
  lat?: number | null;
  lon?: number | null;
  geocode_status?: string | null;
  geocode_reason_code?: string | null;
  geocode_attempt_count?: number | null;
  geocode_candidates?: unknown;
  geocode_next_retry_at?: string | null;
  geocode_last_error?: string | null;
  geocode_last_attempt_at?: string | null;
};
type AddressFixResponse = PageResponse<AddressFix> & {
  geocode_fields_available?: string[];
};
type GapSample = Record<string, unknown> & {
  id: string;
  title: string;
  branch?: string | null;
  status?: string | null;
  missing_fields: string[];
  current_parser?: string | null;
  source_url?: string | null;
  last_seen_at?: string | null;
};
type GapSampleResponse = {
  available: boolean;
  provider: string;
  total: number;
  items: GapSample[];
  missing_counts: Record<string, number>;
  suggested_parser_family: string;
  suggestion_reason: string;
};

function categoryFieldRate(count: number, total: number) {
  if (!total) return 0;
  return Math.round((Number(count || 0) * 1000) / Number(total)) / 10;
}

function QualityRate({ count, total }: { count: number; total: number }) {
  const rate = categoryFieldRate(count, total);
  const tone = rate >= 95 ? 'good' : rate >= 80 ? 'warning' : 'bad';
  return <span className={`quality-rate ${tone}`}>{rate.toLocaleString('ko-KR')}%</span>;
}

function compactValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '-';
  if (Array.isArray(value)) return `${value.length.toLocaleString('ko-KR')}개 후보`;
  const serialized = typeof value === 'object' ? JSON.stringify(value) : String(value);
  return serialized.length > 100 ? `${serialized.slice(0, 97)}...` : serialized;
}

export default function QualityPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const requestedProvider = (searchParams.get('provider') || '').trim().slice(0, 100);
  const session = useOpsSession();
  const queryClient = useQueryClient();
  const [selectedCategory, setSelectedCategory] = useState<CategoryQuality | null>(null);
  const [selectedProvider, setSelectedProvider] = useState<ProviderQuality | null>(null);
  const providerSectionRef = useRef<HTMLElement>(null);
  const summary = useQuery({ queryKey: ['quality-summary'], queryFn: () => opsApi<QualitySummary>('/quality/summary'), refetchInterval: 60_000 });
  const providers = useQuery({
    queryKey: ['quality-providers', selectedCategory?.content_type, selectedCategory?.category, requestedProvider],
    queryFn: () => {
      const params = new URLSearchParams();
      if (selectedCategory) {
        params.set('content_type', selectedCategory.content_type);
        params.set('category', selectedCategory.category);
      }
      if (requestedProvider) params.set('provider', requestedProvider);
      params.set('level', 'major');
      params.set('limit', '500');
      return opsApi<{ available: boolean; items: ProviderQuality[]; total: number }>(
        `/quality/providers?${params.toString()}`,
      );
    },
    enabled: Boolean(selectedCategory || requestedProvider),
  });
  const categories = useQuery({
    queryKey: ['quality-categories', 'major'],
    queryFn: () => opsApi<{ available: boolean; items: CategoryQuality[]; total: number }>('/quality/categories?level=major&limit=10'),
  });
  const gapSamples = useQuery({
    queryKey: ['quality-gap-samples', selectedProvider?.provider, selectedCategory?.category, requestedProvider],
    queryFn: () => {
      const params = new URLSearchParams({ provider: selectedProvider?.provider || '' });
      const contentType = selectedCategory?.content_type || selectedProvider?.content_type;
      if (contentType) params.set('content_type', contentType);
      if (selectedCategory?.category) params.set('category', selectedCategory.category);
      params.set('level', 'major');
      params.set('limit', '10');
      return opsApi<GapSampleResponse>(`/quality/gap-samples?${params.toString()}`);
    },
    enabled: Boolean(selectedProvider && (selectedCategory || requestedProvider)),
  });
  const addressFixes = useQuery({
    queryKey: ['quality-address-fixes', requestedProvider],
    queryFn: () => opsApi<AddressFixResponse>(
      requestedProvider
        ? `/quality/address-fixes?limit=100&provider=${encodeURIComponent(requestedProvider)}`
        : '/quality/address-fixes?limit=100',
    ),
    refetchInterval: 60_000,
  });
  const issues = useQuery({
    queryKey: ['quality-issues', requestedProvider],
    queryFn: () => opsApi<PageResponse<QualityIssue>>(
      requestedProvider
        ? `/quality/issues?limit=100&provider=${encodeURIComponent(requestedProvider)}`
        : '/quality/issues?limit=100',
    ),
    refetchInterval: 30_000,
  });
  const detail = useQuery({
    queryKey: ['quality-issue', id],
    queryFn: () => opsApi<QualityIssue>(`/quality/issues/${id}`),
    enabled: Boolean(id),
  });
  const scan = useMutation({
    mutationFn: () =>
      opsApi('/quality/scan', {
        method: 'POST',
        body: JSON.stringify({ content_type: 'all', max_retries: 0 }),
      }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['jobs'] }),
  });
  const closeIssue = useMutation({
    mutationFn: ({ action, reason }: { action: 'resolve' | 'ignore'; reason: string }) =>
      opsApi(`/quality/issues/${id}/${action}`, {
        method: 'POST',
        body: JSON.stringify({ reason }),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['quality-issues'] });
      void queryClient.invalidateQueries({ queryKey: ['quality-issue', id] });
    },
  });
  const actOnIssue = (action: 'resolve' | 'ignore') => {
    const reason = window.prompt(action === 'resolve' ? '해결 근거를 입력하세요.' : '무시 근거를 입력하세요.');
    if (reason?.trim()) closeIssue.mutate({ action, reason: reason.trim() });
  };
  useEffect(() => {
    if (selectedCategory) {
      providerSectionRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
    }
  }, [selectedCategory]);
  const providerColumns = useMemo<ColumnDef<ProviderQuality>[]>(
    () => [
      { accessorKey: 'provider', header: 'Provider' },
      {
        accessorKey: 'active_count',
        header: '수집 데이터',
        cell: ({ row }) => formatNumber(row.original.active_count),
      },
      {
        accessorKey: 'field_completeness',
        header: '필드 충족',
        cell: ({ row }) => {
          const value = Number(row.original.field_completeness || 0);
          const tone = value >= 95 ? 'good' : value >= 80 ? 'warning' : 'bad';
          return <strong className={`quality-rate ${tone}`}>{value.toLocaleString('ko-KR')}%</strong>;
        },
      },
      {
        accessorKey: 'target_count',
        header: '대상',
        cell: ({ row }) => <QualityRate count={row.original.target_count} total={row.original.active_count} />,
      },
      {
        accessorKey: 'fee_count',
        header: '요금',
        cell: ({ row }) => <QualityRate count={row.original.fee_count} total={row.original.active_count} />,
      },
      {
        accessorKey: 'date_count',
        header: '날짜',
        cell: ({ row }) => <QualityRate count={row.original.date_count} total={row.original.active_count} />,
      },
      {
        accessorKey: 'place_count',
        header: '장소',
        cell: ({ row }) => <QualityRate count={row.original.place_count} total={row.original.active_count} />,
      },
      {
        accessorKey: 'category_count',
        header: '분야',
        cell: ({ row }) => <QualityRate count={row.original.category_count} total={row.original.active_count} />,
      },
      {
        accessorKey: 'time_count',
        header: '시간',
        cell: ({ row }) => <QualityRate count={row.original.time_count} total={row.original.active_count} />,
      },
      {
        accessorKey: 'encoding_issue_count',
        header: '인코딩 손상',
        cell: ({ row }) =>
          row.original.encoding_issue_count ? (
            <span className="quality-damage-count">{formatNumber(row.original.encoding_issue_count)}</span>
          ) : '-',
      },
      {
        id: 'url',
        header: 'Provider URL',
        cell: ({ row }) =>
          row.original.provider_urls?.[0] ? (
            <a href={row.original.provider_urls[0]} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>
              원본 열기
            </a>
          ) : (
            '-'
          ),
      },
    ],
    [],
  );
  const categoryColumns = useMemo<ColumnDef<CategoryQuality>[]>(
    () => [
      {
        accessorKey: 'category',
        header: '대카테고리',
        cell: ({ row }) => (
          <div className="category-quality-name">
            <strong>
              {row.original.category}
              <span aria-hidden="true">›</span>
            </strong>
            {row.original.encoding_issue_count > 0 ? (
              <small>원본 손상 {formatNumber(row.original.encoding_issue_count)}건</small>
            ) : null}
          </div>
        ),
      },
      { accessorKey: 'provider_count', header: 'Provider', cell: ({ row }) => formatNumber(row.original.provider_count) },
      { accessorKey: 'active_count', header: '수집 데이터', cell: ({ row }) => formatNumber(row.original.active_count) },
      {
        accessorKey: 'field_completeness',
        header: '필드 충족',
        cell: ({ row }) => {
          const value = Number(row.original.field_completeness || 0);
          const tone = value >= 95 ? 'good' : value >= 80 ? 'warning' : 'bad';
          return <strong className={`quality-rate ${tone}`}>{value.toLocaleString('ko-KR')}%</strong>;
        },
      },
      {
        accessorKey: 'target_count',
        header: '대상',
        cell: ({ row }) => <QualityRate count={row.original.target_count} total={row.original.active_count} />,
      },
      {
        accessorKey: 'fee_count',
        header: '요금',
        cell: ({ row }) => <QualityRate count={row.original.fee_count} total={row.original.active_count} />,
      },
      {
        accessorKey: 'date_count',
        header: '날짜',
        cell: ({ row }) => <QualityRate count={row.original.date_count} total={row.original.active_count} />,
      },
      {
        accessorKey: 'place_count',
        header: '장소',
        cell: ({ row }) => <QualityRate count={row.original.place_count} total={row.original.active_count} />,
      },
      {
        accessorKey: 'category_count',
        header: '분야',
        cell: ({ row }) => <QualityRate count={row.original.category_count} total={row.original.active_count} />,
      },
      {
        accessorKey: 'time_count',
        header: '시간',
        cell: ({ row }) => <QualityRate count={row.original.time_count} total={row.original.active_count} />,
      },
      {
        accessorKey: 'encoding_issue_count',
        header: '인코딩 손상',
        cell: ({ row }) =>
          row.original.encoding_issue_count ? (
            <span className="quality-damage-count">{formatNumber(row.original.encoding_issue_count)}</span>
          ) : '-',
      },
    ],
    [],
  );
  const gapColumns = useMemo<ColumnDef<GapSample>[]>(
    () => [
      { accessorKey: 'title', header: '샘플 강좌' },
      { accessorKey: 'branch', header: '지점', cell: ({ row }) => row.original.branch || '-' },
      {
        accessorKey: 'missing_fields',
        header: '누락 필드',
        cell: ({ row }) => row.original.missing_fields.join(', '),
      },
      {
        accessorKey: 'current_parser',
        header: '현재 parser',
        cell: ({ row }) => row.original.current_parser || '-',
      },
      {
        id: 'source',
        header: '원본',
        cell: ({ row }) => row.original.source_url ? (
          <a href={row.original.source_url} target="_blank" rel="noreferrer">열기</a>
        ) : '-',
      },
    ],
    [],
  );
  const issueColumns = useMemo<ColumnDef<QualityIssue>[]>(
    () => [
      { accessorKey: 'severity', header: '심각도', cell: ({ row }) => <StatusBadge status={row.original.severity} /> },
      { accessorKey: 'issue_type', header: '유형' },
      { accessorKey: 'content_type', header: '데이터 분류' },
      { accessorKey: 'provider', header: 'Provider', cell: ({ row }) => row.original.provider || '-' },
      { accessorKey: 'branch', header: '지점', cell: ({ row }) => row.original.branch || '-' },
      { accessorKey: 'status', header: '상태', cell: ({ row }) => <StatusBadge status={row.original.status} /> },
      { accessorKey: 'detected_at', header: '탐지', cell: ({ row }) => formatDate(row.original.detected_at) },
    ],
    [],
  );
  const addressFixColumns = useMemo<ColumnDef<AddressFix>[]>(() => {
    const available = new Set(addressFixes.data?.geocode_fields_available || []);
    const columns: ColumnDef<AddressFix>[] = [
      { accessorKey: 'provider', header: 'Provider' },
      { accessorKey: 'name', header: '지점' },
      { accessorKey: 'address', header: '주소', cell: ({ row }) => row.original.address || '-' },
      {
        id: 'coordinates',
        header: '좌표',
        cell: ({ row }) => (
          row.original.lat !== null && row.original.lat !== undefined
          && row.original.lon !== null && row.original.lon !== undefined
            ? `${row.original.lat}, ${row.original.lon}`
            : '-'
        ),
      },
    ];
    if (available.has('geocode_status')) {
      columns.push({
        accessorKey: 'geocode_status',
        header: 'geocode_status',
        cell: ({ row }) => <StatusBadge status={row.original.geocode_status} />,
      });
    }
    if (available.has('geocode_reason_code')) {
      columns.push({
        accessorKey: 'geocode_reason_code',
        header: 'geocode_reason_code',
        cell: ({ row }) => row.original.geocode_reason_code || '-',
      });
    }
    if (available.has('geocode_attempt_count')) {
      columns.push({
        accessorKey: 'geocode_attempt_count',
        header: 'geocode_attempt_count',
        cell: ({ row }) => formatNumber(row.original.geocode_attempt_count),
      });
    }
    if (available.has('geocode_candidates')) {
      columns.push({
        accessorKey: 'geocode_candidates',
        header: 'geocode_candidates',
        cell: ({ row }) => compactValue(row.original.geocode_candidates),
      });
    }
    if (available.has('geocode_next_retry_at')) {
      columns.push({
        accessorKey: 'geocode_next_retry_at',
        header: 'geocode_next_retry_at',
        cell: ({ row }) => formatDate(row.original.geocode_next_retry_at),
      });
    }
    if (available.has('geocode_last_error')) {
      columns.push({
        accessorKey: 'geocode_last_error',
        header: 'geocode_last_error',
        cell: ({ row }) => compactValue(row.original.geocode_last_error),
      });
    }
    if (available.has('geocode_last_attempt_at')) {
      columns.push({
        accessorKey: 'geocode_last_attempt_at',
        header: 'geocode_last_attempt_at',
        cell: ({ row }) => formatDate(row.original.geocode_last_attempt_at),
      });
    }
    return columns;
  }, [addressFixes.data?.geocode_fields_available]);
  const counts = summary.data?.counts || {};
  const categorySummary = useMemo(() => {
    const items = categories.data?.items || [];
    const active = items.reduce((sum, item) => sum + Number(item.active_count || 0), 0);
    const weightedCompleteness = items.reduce(
      (sum, item) => sum + Number(item.field_completeness || 0) * Number(item.active_count || 0),
      0,
    );
    return {
      categoryCount: new Set(items.map((item) => item.category)).size,
      active,
      encodingIssues: items.reduce((sum, item) => sum + Number(item.encoding_issue_count || 0), 0),
      fieldCompleteness: active ? Math.round((weightedCompleteness * 10) / active) / 10 : 0,
    };
  }, [categories.data?.items]);
  const focusedAddressFixes = (addressFixes.data?.items || []).filter(
    (item) => !requestedProvider || item.provider === requestedProvider,
  );
  const focusedIssues = (issues.data?.items || []).filter(
    (item) => !requestedProvider || item.provider === requestedProvider,
  );
  const focusedProviders = (providers.data?.items || []).filter(
    (item) => !requestedProvider || item.provider === requestedProvider,
  );
  const showProviderEvidence = Boolean(selectedCategory || requestedProvider);

  return (
    <>
      <PageHeader
        eyebrow="PRODUCTION DATA CONTRACT"
        title="Data Quality"
        description="운영 서버의 service_group 분류 기준으로 품질을 계산하고, 탐지된 문제의 처리 이력을 남깁니다."
        actions={(
          <>
            <Link className="button subtle" to="/crawler-improvements">개선 큐</Link>
            {session.role !== 'viewer' ? (
              <button className="button primary" type="button" disabled={scan.isPending} onClick={() => scan.mutate()}>
                품질 검사 등록
              </button>
            ) : null}
          </>
        )}
      />
      {requestedProvider ? (
        <div className="data-source-banner">
          <strong>Provider 집중 보기</strong>
          <span>{requestedProvider}</span>
          <small>위치 보정과 품질 문제는 정확히 일치하는 Provider만 표시합니다.</small>
        </div>
      ) : null}
      {(scan.error || closeIssue.error) && <QueryState error={scan.error || closeIssue.error} />}
      <QueryState loading={summary.isLoading} error={summary.error} unavailable={summary.data?.available === false} />
      {summary.data?.available && (
        <section className="stats-grid quality-stats">
          <StatCard label="활성 데이터" value={formatNumber(counts.active_courses)} />
          <StatCard label="필수 필드 누락" value={formatNumber(counts.missing_required)} tone={counts.missing_required ? 'warn' : 'good'} />
          <StatCard label="중복 URL" value={formatNumber(counts.duplicate_urls)} tone={counts.duplicate_urls ? 'warn' : 'good'} />
          <StatCard label="날짜 이상" value={formatNumber(counts.invalid_dates)} tone={counts.invalid_dates ? 'bad' : 'good'} />
          <StatCard
            label="위치 미완성"
            value={formatNumber(
              counts.incomplete_location
              ?? ((counts.missing_address || 0) + (counts.missing_coordinates || 0)),
            )}
            tone="warn"
          />
          <StatCard label="자동 반영 차단" value={formatNumber(counts.blocked_sync)} tone={counts.blocked_sync ? 'bad' : 'neutral'} />
        </section>
      )}
      <section className="panel">
        <header className="section-header">
          <div>
            <h2>위치 보정 상태</h2>
            <small>주소·좌표가 미완성인 지점과 카카오 지오코딩 처리 상태를 표시합니다.</small>
          </div>
        </header>
        <QueryState
          loading={addressFixes.isLoading}
          error={addressFixes.error}
          unavailable={addressFixes.data?.available === false}
          empty={addressFixes.data?.available === true && focusedAddressFixes.length === 0}
        />
        {focusedAddressFixes.length ? (
          <DataTable
            data={focusedAddressFixes}
            columns={addressFixColumns}
            exportName="mooncen-address-fixes.csv"
          />
        ) : null}
      </section>
      <section className="panel">
        <header className="section-header">
          <div>
            <h2>대카테고리별 품질</h2>
            <small>문화센터·체험·교육 행을 선택하면 해당 Provider별 품질이 아래에 표시됩니다.</small>
          </div>
        </header>
        <QueryState
          loading={categories.isLoading}
          error={categories.error}
          unavailable={categories.data?.available === false}
          empty={categories.data?.items.length === 0}
        />
        {categories.data?.items.length ? (
          <>
            <dl className="category-quality-summary">
              <div>
                <dt>대카테고리</dt>
                <dd>{formatNumber(categorySummary.categoryCount)}개</dd>
              </div>
              <div>
                <dt>필드 평균 충족</dt>
                <dd>{categorySummary.fieldCompleteness.toLocaleString('ko-KR')}%</dd>
              </div>
              <div>
                <dt>수집 데이터</dt>
                <dd>{formatNumber(categorySummary.active)}건</dd>
              </div>
              <div>
                <dt>원본 인코딩 손상</dt>
                <dd className={categorySummary.encodingIssues ? 'text-warn' : ''}>
                  {formatNumber(categorySummary.encodingIssues)}건
                </dd>
              </div>
            </dl>
            <DataTable
              data={categories.data.items}
              columns={categoryColumns}
              exportName="mooncen-category-quality.csv"
              onRowClick={(row) => {
                setSelectedCategory(row);
                setSelectedProvider(null);
              }}
              getRowClassName={(row) =>
                selectedCategory?.content_type === row.content_type
                && selectedCategory?.category === row.category
                  ? 'selected-row'
                  : undefined
              }
            />
          </>
        ) : null}
      </section>
      <section className="panel" ref={providerSectionRef}>
        <header className="section-header">
          <div>
            <h2>
              {selectedCategory
                ? `${selectedCategory.category} Provider별 품질`
                : requestedProvider
                  ? `${requestedProvider} Provider별 품질`
                  : 'Provider별 품질'}
            </h2>
            <small>
              {selectedCategory
                ? `${selectedCategory.category} 대카테고리에 속한 Provider의 수집 품질입니다.`
                : requestedProvider
                  ? `${requestedProvider}의 수집 품질 근거입니다.`
                  : '선택된 대카테고리가 없습니다.'}
            </small>
          </div>
        </header>
        {showProviderEvidence ? (
          <QueryState loading={providers.isLoading} error={providers.error} unavailable={providers.data?.available === false} empty={focusedProviders.length === 0} />
        ) : null}
        {showProviderEvidence && focusedProviders.length ? (
          <DataTable
            data={focusedProviders}
            columns={providerColumns}
            exportName="mooncen-provider-quality.csv"
            onRowClick={(row) => {
              setSelectedProvider(row);
            }}
            getRowClassName={(row) => selectedProvider?.provider === row.provider ? 'selected-row' : undefined}
          />
        ) : null}
        {selectedProvider ? (
          <div className="quality-gap-sampler">
            <header className="section-header">
              <div>
                <h3>{selectedProvider.provider} 누락 필드 샘플</h3>
                <small>누락이 많은 실제 행과 권장 parser family를 함께 표시합니다.</small>
              </div>
              <button
                className="button subtle"
                type="button"
                onClick={() => {
                  const params = new URLSearchParams({
                    content_type: selectedProvider.content_type,
                    provider: selectedProvider.provider,
                    state: 'active',
                  });
                  navigate(`/content?${params.toString()}`);
                }}
              >
                전체 콘텐츠 보기
              </button>
            </header>
            <QueryState
              loading={gapSamples.isLoading}
              error={gapSamples.error}
              unavailable={gapSamples.data?.available === false}
              empty={gapSamples.data?.items.length === 0}
            />
            {gapSamples.data ? (
              <>
                <dl className="category-quality-summary">
                  <div>
                    <dt>권장 parser family</dt>
                    <dd>{gapSamples.data.suggested_parser_family}</dd>
                  </div>
                  <div>
                    <dt>누락 행</dt>
                    <dd>{formatNumber(gapSamples.data.total)}건</dd>
                  </div>
                  <div>
                    <dt>추천 근거</dt>
                    <dd>{gapSamples.data.suggestion_reason}</dd>
                  </div>
                </dl>
                {gapSamples.data.items.length ? (
                  <DataTable
                    data={gapSamples.data.items}
                    columns={gapColumns}
                    exportName={`mooncen-${selectedProvider.provider}-gap-samples.csv`}
                  />
                ) : null}
              </>
            ) : null}
          </div>
        ) : null}
      </section>
      <section className="panel">
        <header className="section-header">
          <h2>{requestedProvider ? `${requestedProvider} 품질 문제` : '품질 문제'}</h2>
        </header>
        <QueryState loading={issues.isLoading} error={issues.error} unavailable={issues.data?.available === false} empty={issues.data?.available === true && focusedIssues.length === 0} />
        {focusedIssues.length ? (
          <DataTable data={focusedIssues} columns={issueColumns} exportName="mooncen-quality-issues.csv" onRowClick={(row) => navigate(`/data-quality/${row.id}`)} />
        ) : null}
      </section>
      {id && (
        <DetailPanel title="품질 문제 상세" onClose={() => navigate('/data-quality')}>
          <QueryState loading={detail.isLoading} error={detail.error} />
          {detail.data && (
            <>
              <DefinitionList value={detail.data} />
              {detail.data.source_url ? (
                <a className="button subtle" href={String(detail.data.source_url)} target="_blank" rel="noreferrer">
                  원본 페이지 열기
                </a>
              ) : null}
              {session.role !== 'viewer' && ['open', 'reviewing'].includes(detail.data.status) ? (
                <div className="button-row">
                  <button className="button primary" type="button" onClick={() => actOnIssue('resolve')}>
                    해결 처리
                  </button>
                  <button className="button subtle" type="button" onClick={() => actOnIssue('ignore')}>
                    근거 남기고 무시
                  </button>
                </div>
              ) : null}
            </>
          )}
        </DetailPanel>
      )}
    </>
  );
}
