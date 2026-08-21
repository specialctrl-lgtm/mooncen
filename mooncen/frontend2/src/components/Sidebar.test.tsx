import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  allDays,
  allFees,
  allTimes,
  buildAgeFilterOptions,
  defaultStatusFilters,
  sameStringSet,
} from '../utils/courseFilters';
import Sidebar from './Sidebar';

afterEach(cleanup);

function FilterHarness() {
  const [ageFilters, setAgeFilters] = useState<string[] | null>(null);
  const observedAges = ageFilters ?? ['영아', '유아', '아동', '청소년', '성인', '시니어', '전체', '연령 미정'];
  const ageOptions = buildAgeFilterOptions(observedAges, ageFilters ?? []);

  return (
    <Sidebar
      mapMode="provider"
      categoryOptions={[{ value: '미술·공예', label: '미술·공예' }]}
      ageOptions={ageOptions}
      providerOptions={[]}
      categoryFilters={['미술·공예']}
      ageFilters={ageFilters}
      branchFilterActive={false}
      childAgeMonths=""
      childAgeYears=""
      selectedDate=""
      dayFilters={allDays}
      timeFilters={allTimes}
      providerFilters={[]}
      feeFilters={allFees}
      statusFilters={defaultStatusFilters}
      selectedBranch={null}
      locationValue="기본 위치"
      locationPickerOpen={false}
      detailFilterCount={0}
      onClearSelectedBranch={vi.fn()}
      onBranchSelectAll={vi.fn()}
      onOpenLocationPicker={vi.fn()}
      onCategoryToggle={vi.fn()}
      onCategorySelectAll={vi.fn()}
      onAgeToggle={(value) => {
        const allAges = ageOptions.map((option) => option.value);
        const base = ageFilters ?? allAges;
        const next = sameStringSet(base, allAges)
          ? [value]
          : base.includes(value)
            ? base.filter((age) => age !== value)
            : [...base, value];
        setAgeFilters(next.length ? next : null);
      }}
      onAgeSelectAll={() => setAgeFilters(null)}
      onChildAgeMonthsChange={vi.fn()}
      onChildAgeYearsChange={vi.fn()}
      onSelectedDateChange={vi.fn()}
      onDayToggle={vi.fn()}
      onDaySelectAll={vi.fn()}
      onTimeToggle={vi.fn()}
      onTimeSelectAll={vi.fn()}
      onProviderToggle={vi.fn()}
      onProviderSelectAll={vi.fn()}
      onFeeToggle={vi.fn()}
      onFeeSelectAll={vi.fn()}
      onStatusToggle={vi.fn()}
      onStatusSelectAll={vi.fn()}
      onMapModeChange={vi.fn()}
      onResetFilters={vi.fn()}
    />
  );
}

describe('Sidebar filters', () => {
  it('keeps all age choices visible and checks the selected age after results narrow', () => {
    render(<FilterHarness />);

    fireEvent.click(screen.getByRole('button', { name: /연령/ }));
    fireEvent.click(screen.getByRole('checkbox', { name: '성인' }));

    expect((screen.getByRole('checkbox', { name: '성인' }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole('checkbox', { name: /영아.*0~23개월/ }) as HTMLInputElement).checked).toBe(false);
    expect(screen.getByRole('checkbox', { name: /유아.*만 2~6세/ })).toBeTruthy();
    expect(screen.getByRole('checkbox', { name: /아동.*만 7~13세/ })).toBeTruthy();
    expect((screen.getByRole('checkbox', { name: '전체' }) as HTMLInputElement).checked).toBe(false);
  });
});
