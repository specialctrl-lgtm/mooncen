import '@testing-library/jest-dom/vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { opsApi } from '../api';
import { downloadCsv } from '../utils';
import RegionCoveragePage from './RegionCoveragePage';

vi.mock('../api', () => ({
  opsApi: vi.fn(),
}));

vi.mock('../utils', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../utils')>();
  return {
    ...actual,
    downloadCsv: vi.fn(),
  };
});

const mockedOpsApi = vi.mocked(opsApi);
const mockedDownloadCsv = vi.mocked(downloadCsv);

const unmappedTargets = Array.from({ length: 14 }, (_, index) => ({
  provider: 'TARGET_PROVIDER',
  target_id: `TARGET_${String(index + 1).padStart(2, '0')}`,
  display_name: index === 0 ? '수원 체험 대상' : `미배정 수집 대상 ${index + 1}`,
  region_hint: index === 0 ? '경기도' : null,
  reason: index === 0
    ? 'region_hint_requires_explicit_municipality'
    : 'municipality_evidence_missing',
}));

function scope(
  status: 'collected' | 'historical' | 'connected_empty' | 'unconfigured',
  activeData: number,
  provider = '',
  configuredProvider = provider,
) {
  return {
    status,
    configured_provider_count: configuredProvider ? 1 : 0,
    configured_providers: configuredProvider ? [configuredProvider] : [],
    active_provider_count: provider && activeData ? 1 : 0,
    total_provider_count: provider ? 1 : 0,
    active_data_count: activeData,
    total_data_count: activeData,
    active_branch_count: provider && activeData ? 1 : 0,
    total_branch_count: provider ? 1 : 0,
    latest_collected_at: activeData ? '2026-08-04T12:30:00+00:00' : null,
    latest_historical_at: activeData ? '2026-08-04T12:30:00+00:00' : null,
    providers: provider
      ? [{
          provider,
          active_data_count: activeData,
          total_data_count: activeData,
          active_branch_count: activeData ? 1 : 0,
          total_branch_count: 1,
          latest_collected_at: activeData ? '2026-08-04T12:30:00+00:00' : null,
          latest_historical_at: activeData ? '2026-08-04T12:30:00+00:00' : null,
        }]
      : [],
  };
}

function summary(activeData: number, collected: number) {
  return {
    ...scope(activeData ? 'collected' : 'unconfigured', activeData, activeData ? 'PROVIDER_A' : ''),
    municipality_count: 2,
    collected_municipality_count: collected,
    historical_municipality_count: 0,
    connected_empty_municipality_count: 0,
    unconfigured_municipality_count: 2 - collected,
    configured_provider_count: activeData ? 1 : 0,
    configured_providers: activeData ? ['PROVIDER_A'] : [],
    active_providers: activeData ? ['PROVIDER_A'] : [],
    unmapped_active_data_count: 0,
    unmapped_total_data_count: 0,
    unmapped_provider_count: 0,
    unmapped_provider_names: [],
    unmapped_active_provider_names: [],
  };
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/crawlers/region-coverage']}>
        <Routes>
          <Route path="/crawlers/region-coverage" element={<RegionCoveragePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, queryClient };
}

describe('RegionCoveragePage', () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    localStorage.clear();
    mockedDownloadCsv.mockReset();
    mockedOpsApi.mockClear();
    mockedOpsApi.mockResolvedValue({
      available: true,
      generated_at: '2026-08-04T12:30:00+00:00',
      cache_seconds: 300,
      municipality_source: 'config/municipal_course_search_targets.yaml',
      data_source: {
        environment: 'development',
        is_production: false,
        production_node: 'cloud',
        production_service_host: 'cloud',
        database_host: 'localhost',
        database_name: 'mooncen',
      },
      totals: {
        sido_count: 1,
        municipality_count: 2,
        configured_provider_count: 1,
        experience: {
          ...summary(5, 1),
          total_provider_count: 2,
          total_data_count: 12,
          unmapped_active_data_count: 0,
          unmapped_total_data_count: 7,
          unmapped_provider_count: 1,
          unmapped_provider_names: ['HISTORICAL_ONLY_PROVIDER'],
          unmapped_active_provider_names: [],
          unmapped_configured_provider_count: 1,
          unmapped_configured_providers: ['TARGET_PROVIDER'],
          unmapped_configured_target_count: unmappedTargets.length,
          unmapped_configured_targets: unmappedTargets,
        },
        education: summary(0, 0),
      },
      sidos: [{
        sido: '경기도',
        municipality_count: 2,
        configured_provider_count: 1,
        experience: {
          ...summary(5, 1),
          total_provider_count: 2,
          total_data_count: 12,
          providers: [
            {
              provider: 'PROVIDER_A',
              active_data_count: 5,
              total_data_count: 5,
              active_branch_count: 1,
              total_branch_count: 1,
              latest_collected_at: '2026-08-04T12:30:00+00:00',
              latest_historical_at: '2026-08-04T12:30:00+00:00',
            },
            {
              provider: 'HISTORICAL_PROVIDER',
              active_data_count: 0,
              total_data_count: 7,
              active_branch_count: 0,
              total_branch_count: 2,
              latest_collected_at: null,
              latest_historical_at: '2026-07-30T10:00:00+00:00',
            },
          ],
        },
        education: summary(0, 0),
      }],
      municipalities: [
        {
          code: '4111000000',
          sido: '경기도',
          sigungu: '수원시',
          full_name: '경기도 수원시',
          municipality_type: 'city',
          configured_provider_count: 1,
          configured_providers: ['PROVIDER_PARENT'],
          child_municipality_count: 1,
          rollup: {
            configured_provider_count: 2,
            configured_providers: ['PROVIDER_A', 'PROVIDER_PARENT'],
            experience: {
              ...summary(9, 2),
              active_provider_count: 2,
              total_provider_count: 3,
              active_data_count: 9,
              total_data_count: 17,
            },
            education: summary(0, 0),
          },
          experience: {
            ...scope('collected', 4, 'PROVIDER_PARENT'),
            total_data_count: 6,
          },
          education: scope('unconfigured', 0),
        },
        {
          code: '4111100000',
          sido: '경기도',
          sigungu: '수원시 장안구',
          full_name: '경기도 수원시 장안구',
          municipality_type: 'district',
          configured_provider_count: 1,
          configured_providers: ['PROVIDER_A'],
          child_municipality_count: 0,
          rollup: null,
          experience: scope('collected', 5, 'PROVIDER_A'),
          education: scope('connected_empty', 0, '', 'PROVIDER_EDU'),
        },
        {
          code: '4182000000',
          sido: '경기도',
          sigungu: '가평군',
          full_name: '경기도 가평군',
          municipality_type: 'county',
          configured_provider_count: 0,
          configured_providers: [],
          child_municipality_count: 0,
          rollup: null,
          experience: scope('unconfigured', 0),
          education: scope('unconfigured', 0),
        },
      ],
    });
  });

  it('keeps zero-data municipalities visible and filters them explicitly', async () => {
    renderPage();

    expect(await screen.findByText('수원시 장안구')).toBeInTheDocument();
    expect(screen.getByText('가평군')).toBeInTheDocument();
    expect(screen.getByText('개발 · localhost / mooncen')).toBeInTheDocument();
    expect(screen.getByText(/현재 조회는 운영 데이터가 아닙니다.*cloud/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('상태'), { target: { value: 'empty' } });
    const municipalitySection = screen.getByRole('heading', { name: '시·군·구별 상세 · 체험' }).closest('section');
    expect(municipalitySection).not.toBeNull();
    expect(within(municipalitySection!).queryByText('수원시 장안구')).not.toBeInTheDocument();
    expect(within(municipalitySection!).getByText('가평군')).toBeInTheDocument();
  });

  it('switches the major category to education without dropping zero rows', async () => {
    renderPage();
    await screen.findByText('수원시 장안구');

    fireEvent.click(screen.getByRole('tab', { name: '교육' }));

    expect(screen.getByRole('heading', { name: '시·군·구별 상세 · 교육' })).toBeInTheDocument();
    expect(screen.getByText('수원시 장안구')).toBeInTheDocument();
    expect(screen.getByText('가평군')).toBeInTheDocument();
  });

  it('shows a configured zero-data provider as an explicit zero-count row', async () => {
    renderPage();
    await screen.findByText('수원시 장안구');

    fireEvent.click(screen.getByRole('tab', { name: '교육' }));
    fireEvent.click(screen.getByText('수원시 장안구'));

    const dialog = screen.getByRole('dialog', { name: '경기도 수원시 장안구 · 교육' });
    const providerCell = within(dialog)
      .getAllByText('PROVIDER_EDU')
      .find((element) => element.closest('td[data-label="Provider"]'));
    const providerRow = providerCell?.closest('tr') ?? null;
    expect(providerRow).not.toBeNull();
    expect(providerRow!.querySelector('td[data-label="현재 활성"]')).toHaveTextContent('0');
    expect(providerRow!.querySelector('td[data-label="전체 이력"]')).toHaveTextContent('0');
    expect(providerRow!.querySelector('td[data-label="기관·지점 (활성 / 전체)"]')).toHaveTextContent('0 / 0');
  });

  it('shows unmapped providers and individual collection targets separately', async () => {
    renderPage();

    expect(await screen.findByText('지역을 확정하지 못한 체험 설정 Provider 1개')).toBeInTheDocument();
    const targetSummary = screen.getByText('지역 미배정 체험 수집 target 14개');
    const targetDisclosure = targetSummary.closest('details');
    expect(targetDisclosure).not.toBeNull();

    fireEvent.click(targetSummary);

    expect(within(targetDisclosure!).getByText('수원 체험 대상')).toBeInTheDocument();
    expect(within(targetDisclosure!).getByText('TARGET_01')).toBeInTheDocument();
    expect(within(targetDisclosure!).getByText('경기도')).toBeInTheDocument();
    expect(within(targetDisclosure!).getByText('시도 힌트만 있어 시·군·구 지정 필요')).toBeInTheDocument();
    expect(within(targetDisclosure!).getAllByText('TARGET_PROVIDER')).toHaveLength(14);
  });

  it('shows active and historical totals at national and sido levels', async () => {
    renderPage();
    await screen.findByText('수원시 장안구');

    const stats = screen.getByText('전체 Provider (이력 포함)').closest('.region-stats') as HTMLElement | null;
    expect(stats).not.toBeNull();
    expect(within(stats!).getByText('활성 Provider').closest('article')).toHaveTextContent('1');
    expect(within(stats!).getByText('전체 Provider (이력 포함)').closest('article')).toHaveTextContent('2');
    expect(within(stats!).getByText('활성 데이터').closest('article')).toHaveTextContent('5');
    expect(within(stats!).getByText('전체 데이터 (이력 포함)').closest('article')).toHaveTextContent('12');

    const sidoSection = screen.getByRole('heading', { name: '시도별 요약 · 체험' }).closest('section');
    expect(sidoSection).not.toBeNull();
    expect(within(sidoSection!).getByRole('columnheader', { name: '활성 Provider' })).toBeInTheDocument();
    expect(within(sidoSection!).getByRole('columnheader', { name: '전체 Provider (이력)' })).toBeInTheDocument();
    expect(within(sidoSection!).getByRole('columnheader', { name: '활성 데이터' })).toBeInTheDocument();
    expect(within(sidoSection!).getByRole('columnheader', { name: '전체 데이터 (이력)' })).toBeInTheDocument();
    const sidoRow = within(sidoSection!).getByText('경기도').closest('tr');
    expect(sidoRow).not.toBeNull();
    expect(sidoRow!.querySelector('td[data-label="활성 Provider"]')).toHaveTextContent('1');
    expect(sidoRow!.querySelector('td[data-label="전체 Provider (이력)"]')).toHaveTextContent('2');
    expect(sidoRow!.querySelector('td[data-label="활성 데이터"]')).toHaveTextContent('5');
    expect(sidoRow!.querySelector('td[data-label="전체 데이터 (이력)"]')).toHaveTextContent('12');
  });

  it('uses the rollup totals for a parent city and labels direct values as secondary', async () => {
    renderPage();
    const parentName = await screen.findByText('수원시');
    const parentRow = parentName.closest('tr');
    expect(parentRow).not.toBeNull();

    expect(within(parentRow!).getByText('9')).toBeInTheDocument();
    expect(within(parentRow!).getByText('직접 4')).toBeInTheDocument();
    expect(within(parentRow!).getByText('17')).toBeInTheDocument();
    expect(within(parentRow!).getByText('직접 6')).toBeInTheDocument();
    expect(within(parentRow!).getByText('3')).toBeInTheDocument();
    expect(within(parentRow!).getAllByText('직접 1')).toHaveLength(3);
  });

  it('opens a sido provider detail and keeps it exclusive from municipality detail', async () => {
    renderPage();
    const sidoSection = (await screen.findByRole('heading', { name: '시도별 요약 · 체험' })).closest('section');
    expect(sidoSection).not.toBeNull();
    const sidoRow = within(sidoSection!).getByText('경기도').closest('tr');
    expect(sidoRow).not.toBeNull();

    fireEvent.click(sidoRow!);

    const sidoDialog = screen.getByRole('dialog', { name: '경기도 · 체험 시도 요약' });
    expect(sidoRow).toHaveClass('selected-row');
    expect(within(sidoDialog).getByText('지역 매핑 Provider 1개')).toBeInTheDocument();
    expect(within(sidoDialog).getAllByText('PROVIDER_A')).toHaveLength(2);
    expect(within(sidoDialog).getByText('HISTORICAL_PROVIDER')).toBeInTheDocument();
    const historicalRow = within(sidoDialog).getByText('HISTORICAL_PROVIDER').closest('tr');
    expect(historicalRow).not.toBeNull();
    expect(historicalRow!.querySelector('td[data-label="현재 활성"]')).toHaveTextContent('0');
    expect(historicalRow!.querySelector('td[data-label="전체 이력"]')).toHaveTextContent('7');
    expect(historicalRow!.querySelector('td[data-label="기관·지점 (활성 / 전체)"]')).toHaveTextContent('0 / 2');

    fireEvent.click(screen.getByText('수원시 장안구'));

    expect(screen.queryByRole('dialog', { name: '경기도 · 체험 시도 요약' })).not.toBeInTheDocument();
    expect(screen.getByRole('dialog', { name: '경기도 수원시 장안구 · 체험' })).toBeInTheDocument();
  });

  it('exports stable sido provider names and per-provider count maps', async () => {
    renderPage();
    const sidoSection = (await screen.findByRole('heading', { name: '시도별 요약 · 체험' })).closest('section');
    expect(sidoSection).not.toBeNull();

    fireEvent.click(within(sidoSection!).getByRole('button', { name: 'CSV 내보내기' }));

    expect(mockedDownloadCsv).toHaveBeenCalledTimes(1);
    const [filename, rows] = mockedDownloadCsv.mock.calls[0];
    expect(filename).toBe('mooncen-experience-sido-coverage.csv');
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      sido: '경기도',
      configured_provider_names: 'PROVIDER_A',
      active_provider_names: 'PROVIDER_A',
      historical_only_provider_names: 'HISTORICAL_PROVIDER',
      provider_active_data_count_by_name: {
        HISTORICAL_PROVIDER: 0,
        PROVIDER_A: 5,
      },
      provider_total_data_count_by_name: {
        HISTORICAL_PROVIDER: 7,
        PROVIDER_A: 5,
      },
      provider_active_branch_count_by_name: {
        HISTORICAL_PROVIDER: 0,
        PROVIDER_A: 1,
      },
      provider_total_branch_count_by_name: {
        HISTORICAL_PROVIDER: 2,
        PROVIDER_A: 1,
      },
    });
  });

  it('exports zero-data configured providers and branch counts for municipalities', async () => {
    renderPage();
    await screen.findByText('수원시 장안구');
    fireEvent.click(screen.getByRole('tab', { name: '교육' }));
    const municipalitySection = screen.getByRole('heading', { name: '시·군·구별 상세 · 교육' }).closest('section');
    expect(municipalitySection).not.toBeNull();

    fireEvent.click(within(municipalitySection!).getByRole('button', { name: 'CSV 내보내기' }));

    expect(mockedDownloadCsv).toHaveBeenCalledTimes(1);
    const [filename, rows] = mockedDownloadCsv.mock.calls[0];
    expect(filename).toBe('mooncen-education-municipality-coverage.csv');
    const district = rows.find((row) => row.code === '4111100000');
    expect(district).toMatchObject({
      configured_providers: 'PROVIDER_EDU',
      configured_provider_count: 1,
      display_active_branch_count_non_additive: 0,
      display_total_branch_count_non_additive: 0,
      direct_active_branch_count: 0,
      direct_total_branch_count: 0,
      direct_provider_active_data_count_by_name: { PROVIDER_EDU: 0 },
      direct_provider_total_data_count_by_name: { PROVIDER_EDU: 0 },
      direct_provider_active_branch_count_by_name: { PROVIDER_EDU: 0 },
      direct_provider_total_branch_count_by_name: { PROVIDER_EDU: 0 },
    });
  });

  it('exports parent-city display and direct counts without an additive-looking total column', async () => {
    renderPage();
    await screen.findByText('수원시 장안구');
    const municipalitySection = screen.getByRole('heading', { name: '시·군·구별 상세 · 체험' }).closest('section');
    expect(municipalitySection).not.toBeNull();

    fireEvent.click(within(municipalitySection!).getByRole('button', { name: 'CSV 내보내기' }));

    const [, rows] = mockedDownloadCsv.mock.calls[0];
    const parent = rows.find((row) => row.code === '4111000000');
    expect(parent).toMatchObject({
      display_active_data_count_non_additive: 9,
      display_total_data_count_non_additive: 17,
      direct_active_data_count: 4,
      direct_total_data_count: 6,
      child_inclusive_active_data_count: 9,
      child_inclusive_total_data_count: 17,
    });
    expect(parent).not.toHaveProperty('active_data_count');
    expect(parent).not.toHaveProperty('total_data_count');
  });

  it('warns about unmapped historical-only data and lists its provider', async () => {
    renderPage();

    const label = await screen.findByText('지역 미확정 데이터 (활성 / 전체)');
    const card = label.closest('article');
    expect(card).not.toBeNull();
    expect(card).toHaveClass('tone-bad');
    expect(card).toHaveTextContent('0 / 7');
    expect(card).toHaveTextContent('Provider 0 / 1');

    const providerSummary = screen.getByText('지역 미확정 체험 실제 데이터 Provider 1개 (활성 0개)');
    const disclosure = providerSummary.closest('details');
    expect(disclosure).not.toBeNull();
    fireEvent.click(providerSummary);
    expect(within(disclosure!).getByText('HISTORICAL_ONLY_PROVIDER · 이력만')).toBeInTheDocument();
  });

  it('refreshes an open detail panel from the latest query data by municipality code', async () => {
    const { queryClient } = renderPage();
    fireEvent.click(await screen.findByText('수원시 장안구'));

    const dialog = screen.getByRole('dialog', { name: '경기도 수원시 장안구 · 체험' });
    expect(within(dialog).getByText('활성 데이터').closest('article')).toHaveTextContent('5');

    const refreshed = structuredClone(queryClient.getQueryData(['crawler-region-coverage'])) as {
      municipalities: Array<{
        code: string;
        experience: { active_data_count: number; total_data_count: number };
      }>;
    };
    const selectedMunicipality = refreshed.municipalities.find((row) => row.code === '4111100000');
    expect(selectedMunicipality).toBeDefined();
    selectedMunicipality!.experience.active_data_count = 42;
    selectedMunicipality!.experience.total_data_count = 57;

    act(() => {
      queryClient.setQueryData(['crawler-region-coverage'], refreshed);
    });

    await waitFor(() => {
      expect(within(dialog).getByText('활성 데이터').closest('article')).toHaveTextContent('42');
      expect(within(dialog).getByText('전체 데이터 (이력 포함)').closest('article')).toHaveTextContent('57');
    });
  });

  it('can bypass the server cache when an operator requests an immediate refresh', async () => {
    renderPage();

    const refresh = await screen.findByRole('button', { name: 'DB에서 즉시 갱신' });
    expect(mockedOpsApi).toHaveBeenCalledWith('/crawlers/region-coverage');

    fireEvent.click(refresh);

    await waitFor(() => {
      expect(mockedOpsApi).toHaveBeenCalledWith(
        '/crawlers/region-coverage?refresh=true',
      );
    });
  });
});
