import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { opsApi } from '../api';
import DataTable from '../components/DataTable';
import StatusBadge from '../components/StatusBadge';
import { DefinitionList, DetailPanel, PageHeader, QueryState, StatCard } from '../components/Ui';
import { useOpsSession } from '../context';
import type {
  BranchQualityItem,
  CrawlerQualityItem,
  PageResponse,
  QualitySummary,
  RegressionComparison,
} from '../types';
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

function ScoreBadge({ score, measurable }: { score: number | null | undefined; measurable: boolean }) {
  if (!measurable || score === null || score === undefined) {
    return <span className="cqs-score-badge unmeasured">미측정</span>;
  }
  const tone = score >= 90 ? 'high' : score >= 75 ? 'mid' : 'low';
  return <span className={`cqs-score-badge ${tone}`}>{score.toLocaleString('ko-KR')}점</span>;
}

export default function QualityPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const requestedProvider = (searchParams.get('provider') || '').trim().slice(0, 100);
  const session = useOpsSession();
  const queryClient = useQueryClient();

  const [activeTab, setActiveTab] = useState<'crawlers' | 'categories' | 'regression' | 'address'>(
    requestedProvider ? 'categories' : 'crawlers',
  );
  const [selectedCategory, setSelectedCategory] = useState<CategoryQuality | null>(null);
  const [selectedProvider, setSelectedProvider] = useState<ProviderQuality | null>(null);
  const [branchDetailProvider, setBranchDetailProvider] = useState<string | null>(null);

  const providerSectionRef = useRef<HTMLElement>(null);

  // Core Quality Summary
  const summary = useQuery({
    queryKey: ['quality-summary'],
    queryFn: () => opsApi<QualitySummary>('/quality/summary'),
    refetchInterval: 60_000,
  });

  // Advanced CQS / DQS Crawler Quality List
  const crawlerQualities = useQuery({
    queryKey: ['quality-crawlers', requestedProvider],
    queryFn: () => {
      const params = new URLSearchParams();
      if (requestedProvider) params.set('provider', requestedProvider);
      params.set('limit', '100');
      return opsApi<{ available: boolean; items: CrawlerQualityItem[]; total: number }>(
        `/quality/crawlers?${params.toString()}`,
      );
    },
    refetchInterval: 60_000,
  });

  // Branch level breakdown query
  const branchQualities = useQuery({
    queryKey: ['quality-branches', branchDetailProvider],
    queryFn: () => opsApi<{ available: boolean; provider: string; items: BranchQualityItem[]; total: number }>(
      `/quality/providers/${encodeURIComponent(branchDetailProvider || '')}/branches`,
    ),
    enabled: Boolean(branchDetailProvider),
  });

  // Pre- vs Post-crawler regression comparison query
  const regressions = useQuery({
    queryKey: ['quality-regression', requestedProvider],
    queryFn: () => {
      const params = new URLSearchParams();
      if (requestedProvider) params.set('provider', requestedProvider);
      params.set('limit', '20');
      return opsApi<{ available: boolean; items: RegressionComparison[]; total: number }>(
        `/quality/regression?${params.toString()}`,
      );
    },
    refetchInterval: 60_000,
  });

  // Legacy Providers / Categories / Gap samples
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

  // CQS & DQS Crawler Columns
  const crawlerColumns = useMemo<ColumnDef<CrawlerQualityItem>[]>(
    () => [
      {
        accessorKey: 'provider',
        header: '수집 업체(Provider)',
        cell: ({ row }) => (
          <div>
            <strong>{row.original.provider}</strong>
            <div style={{ fontSize: '11px', color: 'var(--muted)' }}>
              지점 {row.original.branch_count}개 · 강좌 {formatNumber(row.original.active_courses)}건
            </div>
          </div>
        ),
      },
      {
        accessorKey: 'cqs',
        header: 'CQS (크롤러 점수)',
        cell: ({ row }) => (
          <div>
            <ScoreBadge score={row.original.cqs} measurable={row.original.cqs_measurable} />
            <div style={{ fontSize: '11px', color: 'var(--muted)', marginTop: '2px' }}>
              완전성 {row.original.cqs_breakdown.completeness.rate !== null ? `${row.original.cqs_breakdown.completeness.rate}%` : '미측정'}
              {' · '}
              정확성 {row.original.cqs_breakdown.accuracy.measurable ? `${row.original.cqs_breakdown.accuracy.rate}%` : '미검증'}
            </div>
          </div>
        ),
      },
      {
        accessorKey: 'dqs',
        header: 'DQS (데이터 점수)',
        cell: ({ row }) => (
          <div>
            <ScoreBadge score={row.original.dqs} measurable={row.original.dqs_measurable} />
            <span style={{ marginLeft: '6px', fontSize: '11px', fontWeight: 'bold' }}>
              Grade {row.original.dqs_grade}
            </span>
            <div style={{ fontSize: '11px', color: 'var(--muted)', marginTop: '2px' }}>
              필수항목 {row.original.cqs_breakdown.required_fields.rate}% · URL {row.original.cqs_breakdown.application_url.rate}%
            </div>
          </div>
        ),
      },
      {
        accessorKey: 'anomaly',
        header: '수집량 이상 감지',
        cell: ({ row }) => {
          const a = row.original.anomaly;
          const tone =
            a.status === 'CRITICAL' || a.status === 'ZERO_DROP'
              ? 'bad'
              : a.status === 'WARNING'
                ? 'bad'
                : a.status === 'CAUTION'
                  ? 'warning'
                  : 'good';
          return (
            <div>
              <span className={`quality-rate ${tone}`}>{a.status}</span>
              <div style={{ fontSize: '11px', color: 'var(--muted)', marginTop: '2px' }}>
                {a.reason || '-'}
              </div>
            </div>
          );
        },
      },
      {
        id: 'funnel',
        header: '수집 단계 퍼널',
        cell: ({ row }) => {
          const f = row.original.funnel;
          return (
            <div style={{ fontSize: '11px', whiteSpace: 'nowrap' }}>
              <span>목록 {formatNumber(f.listing_count)}</span> →
              <span> 파싱 {formatNumber(f.parsed_count)}</span> →
              <span> 정상 {formatNumber(f.valid_count)}</span>
              {f.duplicate_count > 0 ? (
                <span style={{ color: 'var(--muted)', marginLeft: '4px' }}>
                  (중복 {formatNumber(f.duplicate_count)})
                </span>
              ) : null}
            </div>
          );
        },
      },
      {
        id: 'actions',
        header: '지점 세부 분석',
        cell: ({ row }) => (
          <button
            className="button subtle"
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setBranchDetailProvider(row.original.provider);
            }}
          >
            지점별 품질보기
          </button>
        ),
      },
    ],
    [],
  );

  // Branch Detail Columns
  const branchColumns = useMemo<ColumnDef<BranchQualityItem>[]>(
    () => [
      { accessorKey: 'branch_name', header: '지점명' },
      {
        accessorKey: 'active_courses',
        header: '수집 강좌수',
        cell: ({ row }) => formatNumber(row.original.active_courses),
      },
      {
        accessorKey: 'valid_rate',
        header: '필수 필드 완성율',
        cell: ({ row }) => <span className="quality-rate good">{row.original.valid_rate}%</span>,
      },
      {
        accessorKey: 'url_rate',
        header: '신청 URL 유효율',
        cell: ({ row }) => <span className="quality-rate good">{row.original.url_rate}%</span>,
      },
      {
        accessorKey: 'has_coords',
        header: '좌표 보유',
        cell: ({ row }) => (row.original.has_coords ? '완료' : '누락'),
      },
      {
        accessorKey: 'status',
        header: '상태',
        cell: ({ row }) => <StatusBadge status={row.original.status.toLowerCase()} />,
      },
    ],
    [],
  );

  // Regression Comparison Columns
  const regressionColumns = useMemo<ColumnDef<RegressionComparison>[]>(
    () => [
      { accessorKey: 'target_key', header: '크롤러 대상(Target)' },
      {
        accessorKey: 'verdict',
        header: '회귀 판정',
        cell: ({ row }) => (
          <span className={`regression-verdict ${row.original.verdict}`}>
            {row.original.verdict}
          </span>
        ),
      },
      {
        id: 'diff',
        header: '수집량 변화 (전후 비교)',
        cell: ({ row }) => {
          const delta = row.original.diff.collected_delta;
          const pct = row.original.diff.collected_delta_pct;
          const tone = delta < 0 ? '#b91c1c' : delta > 0 ? '#15803d' : 'var(--muted)';
          return (
            <div>
              <strong>
                이전 {formatNumber(row.original.previous_run.collected_count)}건 → 현재 {formatNumber(row.original.current_run.collected_count)}건
              </strong>
              <div style={{ color: tone, fontSize: '12px', fontWeight: 'bold' }}>
                {delta > 0 ? `+${formatNumber(delta)}` : formatNumber(delta)}건 ({pct > 0 ? `+${pct}` : pct}%)
              </div>
            </div>
          );
        },
      },
      {
        id: 'duration',
        header: '소요시간 변화',
        cell: ({ row }) => (
          <div>
            {row.original.previous_run.duration_seconds}s → {row.original.current_run.duration_seconds}s
            <div style={{ fontSize: '11px', color: 'var(--muted)' }}>
              ({row.original.diff.duration_delta > 0 ? `+${row.original.diff.duration_delta}` : row.original.diff.duration_delta}s)
            </div>
          </div>
        ),
      },
      {
        accessorKey: 'reasons',
        header: '판정 근거 및 사유',
        cell: ({ row }) =>
          row.original.reasons.length > 0 ? (
            <span style={{ color: '#b91c1c', fontWeight: 600 }}>{row.original.reasons.join(', ')}</span>
          ) : (
            '수집량 및 정상동작 유지'
          ),
      },
    ],
    [],
  );

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
        accessorKey: 'time_count',
        header: '시간',
        cell: ({ row }) => <QualityRate count={row.original.time_count} total={row.original.active_count} />,
      },
      {
        accessorKey: 'encoding_issue_count',
        header: '인코딩 손상',
        cell: ({ row }) => (row.original.encoding_issue_count ? <span className="quality-rate bad">{formatNumber(row.original.encoding_issue_count)}건</span> : '-'),
      },
    ],
    [],
  );

  const categoryColumns = useMemo<ColumnDef<CategoryQuality>[]>(
    () => [
      { accessorKey: 'category', header: '대카테고리' },
      {
        accessorKey: 'active_count',
        header: '수집 데이터',
        cell: ({ row }) => formatNumber(row.original.active_count),
      },
      {
        accessorKey: 'provider_count',
        header: 'Provider 수',
        cell: ({ row }) => `${formatNumber(row.original.provider_count)}개`,
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
        accessorKey: 'encoding_issue_count',
        header: '인코딩 손상',
        cell: ({ row }) => (row.original.encoding_issue_count ? <span className="quality-rate bad">원본 손상 {formatNumber(row.original.encoding_issue_count)}건</span> : '-'),
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
        eyebrow="PRODUCTION QUALITY ASSURANCE"
        title="품질 관리 시스템 (Quality Management)"
        description="크롤러 수집 성능(CQS)과 강좌 데이터 품질(DQS)을 독립 평가하고, 코드 수정 전후 회귀(Regression) 및 이상 징후를 추적합니다."
        actions={(
          <>
            <Link className="button subtle" to="/crawler-improvements">개선 큐</Link>
            {session.role !== 'viewer' ? (
              <button className="button primary" type="button" disabled={scan.isPending} onClick={() => scan.mutate()}>
                품질 검사 실행
              </button>
            ) : null}
          </>
        )}
      />

      {requestedProvider ? (
        <div className="data-source-banner">
          <strong>Provider 집중 보기</strong>
          <span>{requestedProvider}</span>
          <small>선택된 업체의 크롤러 품질, 지점 현황, 회귀 테스트 내역을 표시합니다.</small>
        </div>
      ) : null}

      {(scan.error || closeIssue.error) && <QueryState error={scan.error || closeIssue.error} />}
      <QueryState loading={summary.isLoading} error={summary.error} unavailable={summary.data?.available === false} />

      {summary.data?.available && (
        <section className="stats-grid quality-stats">
          <StatCard label="활성 수집 강좌" value={formatNumber(counts.active_courses)} />
          <StatCard label="필수 필드 누락" value={formatNumber(counts.missing_required)} tone={counts.missing_required ? 'warn' : 'good'} />
          <StatCard label="중복 강좌 URL" value={formatNumber(counts.duplicate_urls)} tone={counts.duplicate_urls ? 'warn' : 'good'} />
          <StatCard label="날짜/시간 이상" value={formatNumber(counts.invalid_dates)} tone={counts.invalid_dates ? 'bad' : 'good'} />
          <StatCard
            label="위치 미완성"
            value={formatNumber(
              counts.incomplete_location
              ?? ((counts.missing_address || 0) + (counts.missing_coordinates || 0)),
            )}
            tone="warn"
          />
          <StatCard label="수집 이상 차단" value={formatNumber(counts.blocked_sync)} tone={counts.blocked_sync ? 'bad' : 'neutral'} />
        </section>
      )}

      {/* Navigation Sub-Tabs */}
      <nav className="quality-nav-tabs">
        <button
          className={`quality-tab-button ${activeTab === 'crawlers' ? 'active' : ''}`}
          type="button"
          onClick={() => setActiveTab('crawlers')}
        >
          크롤러 품질 (CQS & DQS)
        </button>
        <button
          className={`quality-tab-button ${activeTab === 'regression' ? 'active' : ''}`}
          type="button"
          onClick={() => setActiveTab('regression')}
        >
          코드 수정 회귀 테스트 (Regression)
        </button>
        <button
          className={`quality-tab-button ${activeTab === 'categories' ? 'active' : ''}`}
          type="button"
          onClick={() => setActiveTab('categories')}
        >
          카테고리/업체별 품질
        </button>
        <button
          className={`quality-tab-button ${activeTab === 'address' ? 'active' : ''}`}
          type="button"
          onClick={() => setActiveTab('address')}
        >
          지점 위치 보정
        </button>
      </nav>

      {/* TAB 1: CRAWLER QUALITY (CQS / DQS) */}
      {activeTab === 'crawlers' && (
        <section className="panel">
          <header className="section-header">
            <div>
              <h2>크롤러별 품질 평가 (Crawler Quality Score & Data Quality Score)</h2>
              <small>
                수집 완전성(30점), 필수필드 완성도(15점), 신청 URL 유효성(10점), 최신성(10점), 중복방지(5점), 실행 안정성(5점) 기준 평가입니다.
                정답 데이터가 없는 데이터 정확성(25점)은 임의 100% 처리하지 않고 '미검증'으로 명시합니다.
              </small>
            </div>
          </header>
          <QueryState
            loading={crawlerQualities.isLoading}
            error={crawlerQualities.error}
            unavailable={crawlerQualities.data?.available === false}
            empty={crawlerQualities.data?.items.length === 0}
          />
          {crawlerQualities.data?.items.length ? (
            <DataTable
              data={crawlerQualities.data.items}
              columns={crawlerColumns}
              exportName="mooncen-crawler-cqs-dqs.csv"
            />
          ) : null}
        </section>
      )}

      {/* TAB 2: REGRESSION COMPARISON */}
      {activeTab === 'regression' && (
        <section className="panel">
          <header className="section-header">
            <div>
              <h2>크롤러 코드 수정 전후 회귀 테스트 (Regression Test Diff)</h2>
              <small>
                Antigravity로 크롤러 코드 수정 전/후 수집량, 소요시간, 상태 변화를 비교하여 수집 누락(20% 이상 급감) 및 오류 발생을 즉시 감지합니다.
              </small>
            </div>
          </header>
          <QueryState
            loading={regressions.isLoading}
            error={regressions.error}
            unavailable={regressions.data?.available === false}
            empty={regressions.data?.items.length === 0}
          />
          {regressions.data?.items.length ? (
            <DataTable
              data={regressions.data.items}
              columns={regressionColumns}
              exportName="mooncen-crawler-regression-diff.csv"
            />
          ) : null}
        </section>
      )}

      {/* TAB 3: CATEGORY / PROVIDER QUALITY */}
      {activeTab === 'categories' && (
        <>
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
        </>
      )}

      {/* Location Fixes Section (Visible on categories and address tab) */}
      {(activeTab === 'categories' || activeTab === 'address') && (
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
      )}

      {/* Branch Hierarchy Detail Modal / Panel */}
      {branchDetailProvider && (
        <DetailPanel title={`${branchDetailProvider} 지점별 품질 상세`} onClose={() => setBranchDetailProvider(null)}>
          <QueryState loading={branchQualities.isLoading} error={branchQualities.error} />
          {branchQualities.data?.items ? (
            <div>
              <div style={{ marginBottom: '12px', color: 'var(--muted)', fontSize: '13px' }}>
                총 {branchQualities.data.items.length}개 지점의 수집 강좌 수 및 필수 필드 충족율입니다.
              </div>
              <DataTable
                data={branchQualities.data.items}
                columns={branchColumns}
                exportName={`mooncen-${branchDetailProvider}-branches.csv`}
              />
            </div>
          ) : null}
        </DetailPanel>
      )}

      {/* Quality Issues List */}
      <section className="panel">
        <header className="section-header">
          <h2>{requestedProvider ? `${requestedProvider} 품질 문제` : '품질 문제'}</h2>
        </header>
        <QueryState loading={issues.isLoading} error={issues.error} unavailable={issues.data?.available === false} empty={issues.data?.available === true && focusedIssues.length === 0} />
        {focusedIssues.length ? (
          <DataTable data={focusedIssues} columns={issueColumns} exportName="mooncen-quality-issues.csv" onRowClick={(row) => navigate(`/data-quality/${row.id}`)} />
        ) : null}
      </section>

      {/* Issue Detail Panel */}
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
