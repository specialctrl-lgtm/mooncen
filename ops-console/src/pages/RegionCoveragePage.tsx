import { useQuery } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { useMemo, useRef, useState } from 'react';
import { opsApi } from '../api';
import DataTable from '../components/DataTable';
import { DetailPanel, PageHeader, QueryState, StatCard } from '../components/Ui';
import { useUrlFilters } from '../hooks/useUrlFilters';
import { formatDate, formatNumber } from '../utils';

type CollectionScope = 'experience' | 'education';
type CoverageStatus = 'collected' | 'historical' | 'connected_empty' | 'unconfigured';

interface ProviderCollection {
  provider: string;
  active_data_count: number;
  total_data_count: number;
  active_branch_count: number;
  total_branch_count: number;
  latest_collected_at?: string | null;
  latest_historical_at?: string | null;
}

interface UnmappedConfiguredTarget {
  provider: string;
  target_id: string;
  display_name?: string | null;
  region_hint?: string | null;
  reason: string;
}

interface ScopeCollection {
  status: CoverageStatus;
  configured_provider_count: number;
  configured_providers: string[];
  active_provider_count: number;
  total_provider_count: number;
  active_data_count: number;
  total_data_count: number;
  active_branch_count: number;
  total_branch_count: number;
  latest_collected_at?: string | null;
  latest_historical_at?: string | null;
  providers: ProviderCollection[];
}

interface ScopeSummary extends ScopeCollection {
  municipality_count: number;
  collected_municipality_count: number;
  historical_municipality_count: number;
  connected_empty_municipality_count: number;
  unconfigured_municipality_count: number;
  active_providers: string[];
  unmapped_active_data_count?: number;
  unmapped_total_data_count?: number;
  unmapped_provider_count?: number;
  unmapped_provider_names?: string[];
  unmapped_active_provider_names?: string[];
  unmapped_configured_provider_count?: number;
  unmapped_configured_providers?: string[];
  unmapped_configured_target_count?: number;
  unmapped_configured_targets?: UnmappedConfiguredTarget[];
}

interface MunicipalityCoverage {
  code: string;
  sido: string;
  sigungu: string;
  full_name: string;
  municipality_type: string;
  configured_provider_count: number;
  configured_providers: string[];
  child_municipality_count: number;
  rollup?: {
    configured_provider_count: number;
    configured_providers: string[];
    experience: ScopeSummary;
    education: ScopeSummary;
  } | null;
  experience: ScopeCollection;
  education: ScopeCollection;
}

interface SidoCoverage {
  sido: string;
  municipality_count: number;
  configured_provider_count: number;
  experience: ScopeSummary;
  education: ScopeSummary;
}

interface RegionCoverageResponse {
  available: boolean;
  generated_at: string;
  cache_seconds: number;
  municipality_source: string;
  data_source: {
    environment: string;
    is_production: boolean;
    production_node: string;
    production_service_host: string;
    database_host?: string | null;
    database_name?: string | null;
  };
  totals: {
    sido_count: number;
    municipality_count: number;
    configured_provider_count: number;
    experience: ScopeSummary;
    education: ScopeSummary;
  };
  sidos: SidoCoverage[];
  municipalities: MunicipalityCoverage[];
}

const filterDefaults = {
  scope: 'experience',
  sido: '',
  status: 'all',
  municipality_type: '',
  query: '',
};

const scopeLabels: Record<CollectionScope, string> = {
  experience: '체험',
  education: '교육',
};

const statusLabels: Record<CoverageStatus, string> = {
  collected: '수집됨',
  historical: '과거 데이터',
  connected_empty: '지역 매핑됨 · 해당 범주 0건',
  unconfigured: '미매핑 · 0건',
};

const municipalityTypeLabels: Record<string, string> = {
  city: '시',
  county: '군',
  district: '구',
};

const unmappedReasonLabels: Record<string, string> = {
  explicit_municipality_unresolved: '명시한 시·군·구를 확인하지 못함',
  region_hint_requires_explicit_municipality: '시도 힌트만 있어 시·군·구 지정 필요',
  municipality_not_inferred: 'target 정보로 시·군·구를 추론하지 못함',
  municipality_evidence_missing: '지역을 판단할 근거가 없음',
};

function unmappedReasonLabel(reason: string): string {
  return unmappedReasonLabels[reason] ?? reason;
}

function scopeFor(row: MunicipalityCoverage, scope: CollectionScope): ScopeCollection | ScopeSummary {
  return row.rollup?.[scope] ?? row[scope];
}

function configuredProvidersFor(row: MunicipalityCoverage, scope: CollectionScope): string[] {
  return row.rollup?.[scope].configured_providers ?? row[scope].configured_providers;
}

function collectionRate(value: ScopeSummary): string {
  if (!value.municipality_count) return '0.0%';
  return `${((value.collected_municipality_count / value.municipality_count) * 100).toFixed(1)}%`;
}

function statusMatches(statusFilter: string, value: ScopeCollection | ScopeSummary): boolean {
  if (statusFilter === 'all') return true;
  if (statusFilter === 'empty') return value.active_data_count === 0;
  return value.status === statusFilter;
}

type ProviderCountField =
  | 'active_data_count'
  | 'total_data_count'
  | 'active_branch_count'
  | 'total_branch_count';

function providerCountMap(
  value: ScopeCollection | ScopeSummary,
  field: ProviderCountField,
): Record<string, number> {
  const actualByName = new Map(value.providers.map((provider) => [provider.provider, provider]));
  const names = Array.from(new Set([
    ...value.configured_providers,
    ...value.providers.map((provider) => provider.provider),
  ])).sort();
  return Object.fromEntries(
    names.map((name) => [name, actualByName.get(name)?.[field] ?? 0]),
  );
}

function providerRowsWithConfigured(
  value: ScopeCollection | ScopeSummary,
): ProviderCollection[] {
  const actualByName = new Map(value.providers.map((provider) => [provider.provider, provider]));
  const names = Array.from(new Set([
    ...value.configured_providers,
    ...value.providers.map((provider) => provider.provider),
  ])).sort();
  return names.map((provider) => actualByName.get(provider) ?? {
    provider,
    active_data_count: 0,
    total_data_count: 0,
    active_branch_count: 0,
    total_branch_count: 0,
    latest_collected_at: null,
    latest_historical_at: null,
  });
}

export default function RegionCoveragePage() {
  const [filters, updateFilters] = useUrlFilters('region-coverage', filterDefaults);
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [selectedSido, setSelectedSido] = useState<string | null>(null);
  const forceRefreshRef = useRef(false);
  const scope: CollectionScope = filters.scope === 'education' ? 'education' : 'experience';
  const coverage = useQuery({
    queryKey: ['crawler-region-coverage'],
    queryFn: () => {
      const forceRefresh = forceRefreshRef.current;
      forceRefreshRef.current = false;
      return opsApi<RegionCoverageResponse>(
        `/crawlers/region-coverage${forceRefresh ? '?refresh=true' : ''}`,
      );
    },
    staleTime: 60_000,
    refetchInterval: 60_000,
  });

  const filteredMunicipalities = useMemo(() => {
    const query = filters.query.trim().toLocaleLowerCase('ko-KR');
    return (coverage.data?.municipalities ?? []).filter((row) => {
      const value = scopeFor(row, scope);
      if (filters.sido && row.sido !== filters.sido) return false;
      if (filters.municipality_type && row.municipality_type !== filters.municipality_type) return false;
      if (!statusMatches(filters.status, value)) return false;
      if (!query) return true;
      const providerNames = [
        ...configuredProvidersFor(row, scope),
        ...value.providers.map((provider) => provider.provider),
      ];
      return [row.code, row.sido, row.sigungu, row.full_name, ...providerNames]
        .join(' ')
        .toLocaleLowerCase('ko-KR')
        .includes(query);
    });
  }, [coverage.data?.municipalities, filters, scope]);

  const municipalityColumns = useMemo<ColumnDef<MunicipalityCoverage>[]>(
    () => [
      { accessorKey: 'sido', header: '시도' },
      {
        accessorKey: 'sigungu',
        header: '시·군·구',
        cell: ({ row }) => (
          <span className="region-name-cell">
            <strong>{row.original.sigungu}</strong>
            <small>{row.original.code}</small>
            {row.original.child_municipality_count > 0 ? (
              <small>하위 구 {formatNumber(row.original.child_municipality_count)}개 포함</small>
            ) : null}
          </span>
        ),
      },
      {
        accessorKey: 'municipality_type',
        header: '유형',
        cell: ({ row }) => municipalityTypeLabels[row.original.municipality_type] || row.original.municipality_type,
      },
      {
        id: 'status',
        header: '상태',
        accessorFn: (row) => scopeFor(row, scope).status,
        cell: ({ row }) => {
          const value = scopeFor(row.original, scope);
          return (
            <span className={`coverage-status coverage-${value.status}`}>
              {statusLabels[value.status]}
            </span>
          );
        },
      },
      {
        id: 'configured_provider_count',
        header: '지역 매핑 Provider',
        accessorFn: (row) => scopeFor(row, scope).configured_provider_count,
        cell: ({ row }) => formatNumber(configuredProvidersFor(row.original, scope).length),
      },
      {
        id: 'active_provider_count',
        header: '활성 Provider',
        accessorFn: (row) => scopeFor(row, scope).active_provider_count,
        cell: ({ row }) => {
          const value = scopeFor(row.original, scope);
          return (
            <span className="region-count-cell">
              <strong>{formatNumber(value.active_provider_count)}</strong>
              {row.original.rollup ? <small>직접 {formatNumber(row.original[scope].active_provider_count)}</small> : null}
            </span>
          );
        },
      },
      {
        id: 'total_provider_count',
        header: '전체 Provider (이력)',
        accessorFn: (row) => scopeFor(row, scope).total_provider_count,
        cell: ({ row }) => {
          const value = scopeFor(row.original, scope);
          return (
            <span className="region-count-cell">
              <strong>{formatNumber(value.total_provider_count)}</strong>
              {row.original.rollup ? <small>직접 {formatNumber(row.original[scope].total_provider_count)}</small> : null}
            </span>
          );
        },
      },
      {
        id: 'active_branch_count',
        header: '기관·지점',
        accessorFn: (row) => scopeFor(row, scope).active_branch_count,
        cell: ({ row }) => {
          const value = scopeFor(row.original, scope);
          return (
            <span className="region-count-cell">
              <strong>{formatNumber(value.active_branch_count)}</strong>
              {row.original.rollup ? <small>직접 {formatNumber(row.original[scope].active_branch_count)}</small> : null}
            </span>
          );
        },
      },
      {
        id: 'active_data_count',
        header: '활성 데이터',
        accessorFn: (row) => scopeFor(row, scope).active_data_count,
        cell: ({ row }) => {
          const value = scopeFor(row.original, scope);
          return (
            <span className="region-count-cell">
              <strong>{formatNumber(value.active_data_count)}</strong>
              {row.original.rollup ? <small>직접 {formatNumber(row.original[scope].active_data_count)}</small> : null}
            </span>
          );
        },
      },
      {
        id: 'total_data_count',
        header: '전체 데이터 (이력)',
        accessorFn: (row) => scopeFor(row, scope).total_data_count,
        cell: ({ row }) => {
          const value = scopeFor(row.original, scope);
          return (
            <span className="region-count-cell">
              <strong>{formatNumber(value.total_data_count)}</strong>
              {row.original.rollup ? <small>직접 {formatNumber(row.original[scope].total_data_count)}</small> : null}
            </span>
          );
        },
      },
      {
        id: 'latest_collected_at',
        header: '최근 원본 확인',
        accessorFn: (row) => scopeFor(row, scope).latest_collected_at || '',
        cell: ({ row }) => formatDate(scopeFor(row.original, scope).latest_collected_at),
      },
    ],
    [scope],
  );

  const sidoColumns = useMemo<ColumnDef<SidoCoverage>[]>(
    () => [
      { accessorKey: 'sido', header: '시도' },
      { accessorKey: 'municipality_count', header: '대상 지역', cell: ({ row }) => formatNumber(row.original.municipality_count) },
      {
        id: 'collected',
        header: '수집 지역',
        accessorFn: (row) => row[scope].collected_municipality_count,
        cell: ({ row }) => `${formatNumber(row.original[scope].collected_municipality_count)} / ${collectionRate(row.original[scope])}`,
      },
      {
        id: 'empty',
        header: '현재 0건',
        accessorFn: (row) => row.municipality_count - row[scope].collected_municipality_count,
        cell: ({ row }) => formatNumber(row.original.municipality_count - row.original[scope].collected_municipality_count),
      },
      {
        id: 'configured_providers',
        header: '지역 매핑 Provider',
        accessorFn: (row) => row[scope].configured_provider_count,
        cell: ({ row }) => formatNumber(row.original[scope].configured_provider_count),
      },
      {
        id: 'active_providers',
        header: '활성 Provider',
        accessorFn: (row) => row[scope].active_provider_count,
        cell: ({ row }) => formatNumber(row.original[scope].active_provider_count),
      },
      {
        id: 'total_providers',
        header: '전체 Provider (이력)',
        accessorFn: (row) => row[scope].total_provider_count,
        cell: ({ row }) => formatNumber(row.original[scope].total_provider_count),
      },
      {
        id: 'active_data',
        header: '활성 데이터',
        accessorFn: (row) => row[scope].active_data_count,
        cell: ({ row }) => formatNumber(row.original[scope].active_data_count),
      },
      {
        id: 'total_data',
        header: '전체 데이터 (이력)',
        accessorFn: (row) => row[scope].total_data_count,
        cell: ({ row }) => formatNumber(row.original[scope].total_data_count),
      },
      {
        id: 'latest',
        header: '최근 원본 확인',
        accessorFn: (row) => row[scope].latest_collected_at || '',
        cell: ({ row }) => formatDate(row.original[scope].latest_collected_at),
      },
    ],
    [scope],
  );

  const providerColumns = useMemo<ColumnDef<ProviderCollection>[]>(
    () => [
      { accessorKey: 'provider', header: 'Provider' },
      { accessorKey: 'active_data_count', header: '현재 활성', cell: ({ row }) => formatNumber(row.original.active_data_count) },
      { accessorKey: 'total_data_count', header: '전체 이력', cell: ({ row }) => formatNumber(row.original.total_data_count) },
      {
        id: 'branch_count',
        header: '기관·지점 (활성 / 전체)',
        accessorFn: (row) => row.active_branch_count,
        cell: ({ row }) => `${formatNumber(row.original.active_branch_count)} / ${formatNumber(row.original.total_branch_count)}`,
      },
      {
        id: 'latest_source_seen_at',
        header: '최근 원본 확인',
        accessorFn: (row) => row.latest_collected_at || row.latest_historical_at || '',
        cell: ({ row }) => formatDate(row.original.latest_collected_at || row.original.latest_historical_at),
      },
    ],
    [],
  );

  const unmappedTargetColumns = useMemo<ColumnDef<UnmappedConfiguredTarget>[]>(
    () => [
      { accessorKey: 'provider', header: 'Provider' },
      {
        accessorKey: 'display_name',
        header: '수집 target',
        cell: ({ row }) => (
          <span className="region-name-cell">
            <strong>{row.original.display_name || row.original.target_id}</strong>
            {row.original.display_name ? <small>{row.original.target_id}</small> : null}
          </span>
        ),
      },
      {
        accessorKey: 'region_hint',
        header: '지역 힌트',
        cell: ({ row }) => row.original.region_hint || '-',
      },
      {
        accessorKey: 'reason',
        header: '미배정 사유',
        cell: ({ row }) => (
          <span title={row.original.reason}>{unmappedReasonLabel(row.original.reason)}</span>
        ),
      },
    ],
    [],
  );

  const totals = coverage.data?.totals[scope];
  const unmappedTargets = totals?.unmapped_configured_targets ?? [];
  const unmappedTargetCount = totals?.unmapped_configured_target_count ?? unmappedTargets.length;
  const unmappedActiveProviderNames = totals?.unmapped_active_provider_names ?? [];
  const unmappedProviderNames = Array.from(new Set([
    ...(totals?.unmapped_provider_names ?? []),
    ...unmappedActiveProviderNames,
  ]));
  const unmappedProviderCount = Math.max(
    totals?.unmapped_provider_count ?? 0,
    unmappedProviderNames.length,
  );
  const unmappedActiveProviderSet = new Set(unmappedActiveProviderNames);
  const hasUnmappedCoverage = Boolean(
    totals?.unmapped_active_data_count
    || totals?.unmapped_total_data_count
    || unmappedProviderCount
    || totals?.unmapped_configured_provider_count
    || unmappedTargetCount,
  );
  const selected = selectedCode
    ? coverage.data?.municipalities.find((row) => row.code === selectedCode) ?? null
    : null;
  const selectedScope = selected ? scopeFor(selected, scope) : null;
  const selectedSidoCoverage = selectedSido
    ? coverage.data?.sidos.find((row) => row.sido === selectedSido) ?? null
    : null;
  const selectedSidoScope = selectedSidoCoverage?.[scope] ?? null;
  const sidoExportRows = useMemo(
    () => (coverage.data?.sidos ?? []).map((row) => {
      const value = row[scope];
      return {
        sido: row.sido,
        municipality_count: row.municipality_count,
        collected_municipality_count: value.collected_municipality_count,
        collection_rate: collectionRate(value),
        configured_provider_count: value.configured_provider_count,
        configured_provider_names: value.configured_providers.join('|'),
        active_provider_count: value.active_provider_count,
        active_provider_names: value.providers
          .filter((provider) => provider.active_data_count > 0)
          .map((provider) => provider.provider)
          .join('|'),
        total_provider_count: value.total_provider_count,
        historical_only_provider_names: value.providers
          .filter((provider) => provider.active_data_count === 0)
          .map((provider) => provider.provider)
          .join('|'),
        active_data_count: value.active_data_count,
        total_data_count: value.total_data_count,
        provider_active_data_count_by_name: providerCountMap(value, 'active_data_count'),
        provider_total_data_count_by_name: providerCountMap(value, 'total_data_count'),
        provider_active_branch_count_by_name: providerCountMap(value, 'active_branch_count'),
        provider_total_branch_count_by_name: providerCountMap(value, 'total_branch_count'),
        latest_source_seen_at: value.latest_collected_at ?? '',
      };
    }),
    [coverage.data?.sidos, scope],
  );
  const municipalityExportRows = useMemo(
    () => filteredMunicipalities.map((row) => {
      const visible = scopeFor(row, scope);
      const direct = row[scope];
      return {
        code: row.code,
        sido: row.sido,
        sigungu: row.sigungu,
        municipality_type: row.municipality_type,
        status: visible.status,
        configured_providers: configuredProvidersFor(row, scope).join('|'),
        configured_provider_count: configuredProvidersFor(row, scope).length,
        active_providers: visible.providers
          .filter((provider) => provider.active_data_count > 0)
          .map((provider) => provider.provider)
          .join('|'),
        active_provider_count: visible.active_provider_count,
        total_provider_count: visible.total_provider_count,
        direct_active_provider_count: direct.active_provider_count,
        direct_total_provider_count: direct.total_provider_count,
        display_active_data_count_non_additive: visible.active_data_count,
        display_total_data_count_non_additive: visible.total_data_count,
        direct_active_data_count: direct.active_data_count,
        child_inclusive_active_data_count: row.rollup?.[scope].active_data_count ?? '',
        direct_total_data_count: direct.total_data_count,
        child_inclusive_total_data_count: row.rollup?.[scope].total_data_count ?? '',
        display_active_branch_count_non_additive: visible.active_branch_count,
        display_total_branch_count_non_additive: visible.total_branch_count,
        direct_active_branch_count: direct.active_branch_count,
        direct_total_branch_count: direct.total_branch_count,
        child_inclusive_active_branch_count: row.rollup?.[scope].active_branch_count ?? '',
        child_inclusive_total_branch_count: row.rollup?.[scope].total_branch_count ?? '',
        direct_provider_active_data_count_by_name: providerCountMap(direct, 'active_data_count'),
        direct_provider_total_data_count_by_name: providerCountMap(direct, 'total_data_count'),
        direct_provider_active_branch_count_by_name: providerCountMap(direct, 'active_branch_count'),
        direct_provider_total_branch_count_by_name: providerCountMap(direct, 'total_branch_count'),
        child_inclusive_provider_active_data_count_by_name: row.rollup
          ? providerCountMap(row.rollup[scope], 'active_data_count')
          : {},
        child_inclusive_provider_total_data_count_by_name: row.rollup
          ? providerCountMap(row.rollup[scope], 'total_data_count')
          : {},
        child_inclusive_provider_active_branch_count_by_name: row.rollup
          ? providerCountMap(row.rollup[scope], 'active_branch_count')
          : {},
        child_inclusive_provider_total_branch_count_by_name: row.rollup
          ? providerCountMap(row.rollup[scope], 'total_branch_count')
          : {},
        latest_source_seen_at: visible.latest_collected_at ?? '',
      };
    }),
    [filteredMunicipalities, scope],
  );

  return (
    <>
      <PageHeader
        eyebrow="COLLECTION COVERAGE"
        title="지역별 수집 현황"
        description="체험·교육 대카테고리를 전국 시·군·구 행정구역 기준으로 확인합니다. 데이터가 0건인 지역도 목록에서 제외하지 않습니다."
        actions={(
          <button
            className="button subtle"
            type="button"
            disabled={coverage.isFetching}
            onClick={() => {
              forceRefreshRef.current = true;
              void coverage.refetch();
            }}
          >
            {coverage.isFetching ? '갱신 중…' : 'DB에서 즉시 갱신'}
          </button>
        )}
      />
      <QueryState loading={coverage.isLoading} error={coverage.error} unavailable={coverage.data?.available === false} />
      {coverage.data?.available ? (
        <>
          <section className={`data-source-banner ${coverage.data.data_source.is_production ? '' : 'warning'}`}>
            <strong>조회 데이터</strong>
            <span>
              {coverage.data.data_source.is_production
                ? `운영 · ${coverage.data.data_source.production_service_host}`
                : `개발 · ${coverage.data.data_source.database_host || 'unknown'} / ${coverage.data.data_source.database_name || 'unknown'}`}
            </span>
            {!coverage.data.data_source.is_production ? (
              <small>
                현재 조회는 운영 데이터가 아닙니다. 운영 DB 위치: {coverage.data.data_source.production_node}
                {' · '}{coverage.data.data_source.production_service_host}
              </small>
            ) : (
              <small>DB 연결 {coverage.data.data_source.database_host || 'unknown'} / {coverage.data.data_source.database_name || 'unknown'}</small>
            )}
            <small>집계 {formatDate(coverage.data.generated_at)} · 최대 {formatNumber(coverage.data.cache_seconds)}초 변경 감지 캐시</small>
          </section>

          <div className="scope-tabs" role="tablist" aria-label="대카테고리">
            {(['experience', 'education'] as CollectionScope[]).map((value) => (
              <button
                key={value}
                type="button"
                role="tab"
                aria-selected={scope === value}
                className={scope === value ? 'active' : ''}
                onClick={() => updateFilters({ scope: value })}
              >
                {scopeLabels[value]}
              </button>
            ))}
          </div>

          {totals ? (
            <section className="stats-grid region-stats">
              <StatCard label="전체 시·군·구" value={formatNumber(coverage.data.totals.municipality_count)} note={`${formatNumber(coverage.data.totals.sido_count)}개 시도`} />
              <StatCard label="수집 지역" value={formatNumber(totals.collected_municipality_count)} note={collectionRate(totals)} tone="good" />
              <StatCard label="현재 0건 지역" value={formatNumber(totals.municipality_count - totals.collected_municipality_count)} tone="warn" />
              <StatCard label="활성 Provider" value={formatNumber(totals.active_provider_count)} />
              <StatCard label="전체 Provider (이력 포함)" value={formatNumber(totals.total_provider_count)} note={`지역 매핑 ${formatNumber(totals.configured_provider_count)}`} />
              <StatCard label="활성 데이터" value={formatNumber(totals.active_data_count)} />
              <StatCard label="전체 데이터 (이력 포함)" value={formatNumber(totals.total_data_count)} />
              <StatCard
                label="지역 미확정 데이터 (활성 / 전체)"
                value={`${formatNumber(totals.unmapped_active_data_count)} / ${formatNumber(totals.unmapped_total_data_count)}`}
                note={`Provider ${formatNumber(unmappedActiveProviderNames.length)} / ${formatNumber(unmappedProviderCount)} · 설정 ${formatNumber(totals.unmapped_configured_provider_count)} · target ${formatNumber(unmappedTargetCount)}`}
                tone={hasUnmappedCoverage ? 'bad' : 'good'}
              />
            </section>
          ) : null}

          {unmappedProviderCount ? (
            <details className="provider-disclosure panel-inline-disclosure">
              <summary>
                지역 미확정 {scopeLabels[scope]} 실제 데이터 Provider{' '}
                {formatNumber(unmappedProviderCount)}개 (활성 {formatNumber(unmappedActiveProviderNames.length)}개)
              </summary>
              {unmappedProviderNames.length ? (
                <div className="provider-chip-list">
                  {unmappedProviderNames.map((provider) => (
                    <code key={provider}>
                      {provider} · {unmappedActiveProviderSet.has(provider) ? '활성' : '이력만'}
                    </code>
                  ))}
                </div>
              ) : (
                <p className="form-note">API 응답에 상세 Provider 목록이 없습니다.</p>
              )}
            </details>
          ) : null}

          {totals?.unmapped_configured_providers?.length ? (
            <details className="provider-disclosure panel-inline-disclosure">
              <summary>
                지역을 확정하지 못한 {scopeLabels[scope]} 설정 Provider{' '}
                {formatNumber(totals.unmapped_configured_providers.length)}개
              </summary>
              <div className="provider-chip-list">
                {totals.unmapped_configured_providers.map((provider) => <code key={provider}>{provider}</code>)}
              </div>
            </details>
          ) : null}

          {unmappedTargetCount ? (
            <details className="provider-disclosure panel-inline-disclosure">
              <summary>
                지역 미배정 {scopeLabels[scope]} 수집 target {formatNumber(unmappedTargetCount)}개
              </summary>
              <p className="form-note">
                설정 Provider 목록과 별개로, 시·군·구를 확정하지 못한 개별 수집 target입니다.
                URL 없이 target 식별 정보와 지역 판단 사유만 표시합니다.
              </p>
              {unmappedTargets.length ? (
                <div className="unmapped-target-table">
                  <DataTable
                    data={unmappedTargets}
                    columns={unmappedTargetColumns}
                    exportName={`mooncen-${scope}-unmapped-configured-targets.csv`}
                  />
                </div>
              ) : (
                <p className="form-note">API 응답에 상세 target 목록이 없습니다.</p>
              )}
            </details>
          ) : null}

          <section className="panel">
            <header className="section-header">
              <div>
                <h2>시도별 요약 · {scopeLabels[scope]}</h2>
                <small>Provider 수는 시도 안에서 중복 제거하고, 활성 데이터 합계는 하위 구와 중복되지 않는 직접 귀속 건수입니다.</small>
              </div>
            </header>
            <DataTable
              data={coverage.data.sidos}
              columns={sidoColumns}
              exportName={`mooncen-${scope}-sido-coverage.csv`}
              exportData={sidoExportRows}
              onRowClick={(row) => {
                updateFilters({ sido: row.sido });
                setSelectedSido(row.sido);
                setSelectedCode(null);
              }}
              getRowClassName={(row) => (
                selectedSido === row.sido || (!selectedSido && filters.sido === row.sido)
                  ? 'selected-row'
                  : undefined
              )}
            />
          </section>

          <section className="panel">
            <header className="section-header">
              <div>
                <h2>시·군·구별 상세 · {scopeLabels[scope]}</h2>
                <small>일반시는 하위 구 포함 합계와 직접 귀속 수를 구분합니다. 일반시 합계와 하위 구 행을 다시 더하면 중복됩니다.</small>
              </div>
            </header>
            <div className="filter-row region-filter-row">
              <label>
                시도
                <select
                  value={filters.sido}
                  onChange={(event) => {
                    updateFilters({ sido: event.target.value });
                    setSelectedSido(null);
                    setSelectedCode(null);
                  }}
                >
                  <option value="">전체 시도</option>
                  {coverage.data.sidos.map((row) => <option key={row.sido} value={row.sido}>{row.sido}</option>)}
                </select>
              </label>
              <label>
                상태
                <select value={filters.status} onChange={(event) => updateFilters({ status: event.target.value })}>
                  <option value="all">전체 · 0건 포함</option>
                  <option value="collected">수집됨</option>
                  <option value="empty">현재 0건</option>
                  <option value="connected_empty">지역 매핑됨 · 해당 범주 0건</option>
                  <option value="historical">과거 데이터만</option>
                  <option value="unconfigured">미매핑 · 0건</option>
                </select>
              </label>
              <label>
                유형
                <select value={filters.municipality_type} onChange={(event) => updateFilters({ municipality_type: event.target.value })}>
                  <option value="">시·군·구 전체</option>
                  <option value="city">시</option>
                  <option value="county">군</option>
                  <option value="district">구</option>
                </select>
              </label>
              <label className="filter-grow">
                지역·Provider 검색
                <input value={filters.query} onChange={(event) => updateFilters({ query: event.target.value })} placeholder="지역명, 코드, Provider" />
              </label>
              <button
                className="button subtle"
                type="button"
                onClick={() => {
                  updateFilters({ ...filterDefaults, scope });
                  setSelectedSido(null);
                  setSelectedCode(null);
                }}
              >
                필터 초기화
              </button>
            </div>
            <QueryState empty={filteredMunicipalities.length === 0} />
            {filteredMunicipalities.length ? (
              <DataTable
                data={filteredMunicipalities}
                columns={municipalityColumns}
                exportName={`mooncen-${scope}-municipality-coverage.csv`}
                exportData={municipalityExportRows}
                onRowClick={(row) => {
                  setSelectedCode(row.code);
                  setSelectedSido(null);
                }}
              />
            ) : null}
          </section>
        </>
      ) : null}

      {selectedSidoCoverage && selectedSidoScope && !selected ? (
        <DetailPanel
          title={`${selectedSidoCoverage.sido} · ${scopeLabels[scope]} 시도 요약`}
          onClose={() => setSelectedSido(null)}
        >
          <section className="region-detail-summary">
            <StatCard label="대상 시·군·구" value={formatNumber(selectedSidoScope.municipality_count)} />
            <StatCard label="수집 지역" value={formatNumber(selectedSidoScope.collected_municipality_count)} note={collectionRate(selectedSidoScope)} />
            <StatCard label="활성 데이터" value={formatNumber(selectedSidoScope.active_data_count)} />
            <StatCard label="전체 데이터 (이력 포함)" value={formatNumber(selectedSidoScope.total_data_count)} />
            <StatCard label="활성 Provider" value={formatNumber(selectedSidoScope.active_provider_count)} />
            <StatCard label="전체 Provider (이력 포함)" value={formatNumber(selectedSidoScope.total_provider_count)} />
          </section>
          <details className="provider-disclosure">
            <summary>지역 매핑 Provider {formatNumber(selectedSidoScope.configured_providers.length)}개</summary>
            <div className="provider-chip-list">
              {selectedSidoScope.configured_providers.length
                ? selectedSidoScope.configured_providers.map((provider) => <code key={provider}>{provider}</code>)
                : <span>연결된 Provider가 없습니다.</span>}
            </div>
          </details>
          <h3>Provider별 수집 내역</h3>
          <QueryState empty={providerRowsWithConfigured(selectedSidoScope).length === 0} />
          {providerRowsWithConfigured(selectedSidoScope).length ? (
            <DataTable
              data={providerRowsWithConfigured(selectedSidoScope)}
              columns={providerColumns}
              exportName={`mooncen-${scope}-${selectedSidoCoverage.sido}-providers.csv`}
            />
          ) : null}
          <p className="form-note">
            시도 안에서 Provider는 중복 제거하고, 데이터 수는 각 시·군·구에 직접 귀속된 건만 합산합니다.
          </p>
        </DetailPanel>
      ) : null}

      {selected && selectedScope ? (
        <DetailPanel title={`${selected.full_name} · ${scopeLabels[scope]}`} onClose={() => setSelectedCode(null)}>
          <section className="region-detail-summary">
            <StatCard label="활성 데이터" value={formatNumber(selectedScope.active_data_count)} note={selected.rollup ? `직접 ${formatNumber(selected[scope].active_data_count)}` : undefined} />
            <StatCard label="전체 데이터 (이력 포함)" value={formatNumber(selectedScope.total_data_count)} note={selected.rollup ? `직접 ${formatNumber(selected[scope].total_data_count)}` : undefined} />
            <StatCard label="활성 Provider" value={formatNumber(selectedScope.active_provider_count)} note={selected.rollup ? `직접 ${formatNumber(selected[scope].active_provider_count)}` : undefined} />
            <StatCard label="전체 Provider (이력 포함)" value={formatNumber(selectedScope.total_provider_count)} note={selected.rollup ? `직접 ${formatNumber(selected[scope].total_provider_count)}` : undefined} />
            <StatCard label="기관·지점" value={formatNumber(selectedScope.active_branch_count)} />
            <StatCard label="최근 원본 확인" value={formatDate(selectedScope.latest_collected_at)} />
          </section>
          <p className="form-note">
            행정구역 코드 {selected.code} · {municipalityTypeLabels[selected.municipality_type] || selected.municipality_type}
            {selected.rollup ? ` · 하위 구 ${selected.child_municipality_count}개 포함` : ''}
          </p>
          <details className="provider-disclosure">
            <summary>지역 매핑 Provider {formatNumber(configuredProvidersFor(selected, scope).length)}개</summary>
            <div className="provider-chip-list">
              {configuredProvidersFor(selected, scope).length
                ? configuredProvidersFor(selected, scope).map((provider) => <code key={provider}>{provider}</code>)
                : <span>연결된 Provider가 없습니다.</span>}
            </div>
          </details>
          <h3>Provider별 수집 내역</h3>
          <QueryState empty={providerRowsWithConfigured(selectedScope).length === 0} />
          {providerRowsWithConfigured(selectedScope).length ? (
            <DataTable
              data={providerRowsWithConfigured(selectedScope)}
              columns={providerColumns}
              exportName={`mooncen-${scope}-${selected.code}-providers.csv`}
            />
          ) : null}
          <p className="form-note">지역 매핑 Provider와 실제 Provider·데이터 수는 선택한 범주만 집계합니다. 최근 원본 확인은 last_seen_at을 우선하고 없으면 최초 확인·생성 시각을 사용합니다.</p>
        </DetailPanel>
      ) : null}
    </>
  );
}
