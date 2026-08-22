import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Branch } from '../api';
import LocationPickerModal from './LocationPickerModal';


vi.mock('./MapSection', () => ({
  default: () => <div data-testid="location-map" />,
}));


afterEach(() => cleanup());


const branch: Branch = {
  id: 'branch-1',
  name: '테스트 지점',
  provider: 'TEST',
  provider_label: '테스트 문화센터',
  address: '서울시 중구 테스트로 1',
  phone: '02-1234-5678',
  lat: 37.5665,
  lon: 126.978,
  website_url: 'https://example.com/branch',
  operating_hours: '월~금 09:00~18:00',
  regular_holiday: '매주 일요일',
  admission_fee: '무료',
  active_course_count: 12,
  open_course_count: 5,
};


function renderPicker(onBranchToggle = vi.fn()) {
  render(
    <LocationPickerModal
      open
      branches={[branch]}
      providerFilters={['TEST']}
      categoryFilters={[]}
      mapMode="provider"
      branchCourseCounts={{ 'branch-1': 12 }}
      branchOpenCounts={{ 'branch-1': 5 }}
      branchUrgentCounts={{ 'branch-1': 2 }}
      favoriteBranchIds={[]}
      selectedBranchIds={[]}
      userLocation={{ lat: 37.5665, lon: 126.978, label: '현재 위치', detected: true }}
      myLocation={null}
      locationLabel="현재 위치"
      locationError={null}
      diameterKm={10}
      locating={false}
      usingCurrentLocation
      debugMode={false}
      onClose={vi.fn()}
      onBranchToggle={onBranchToggle}
      onClearBranches={vi.fn()}
      onDiameterChange={vi.fn()}
      onRequestCurrentLocation={vi.fn()}
      onResetLocation={vi.fn()}
      onMapCenterChange={vi.fn()}
      onVisibleBranchIdsChange={vi.fn()}
    />,
  );
  return onBranchToggle;
}


describe('LocationPickerModal branch details', () => {
  it('shows the selected branch information and actions', () => {
    const onBranchToggle = renderPicker();

    fireEvent.click(screen.getByRole('button', { name: /테스트 지점/ }));

    expect(onBranchToggle).toHaveBeenCalledWith(branch);
    expect(screen.getByRole('complementary', { name: '선택 지점 정보' })).toBeTruthy();
    expect(screen.getAllByText('서울시 중구 테스트로 1')).toHaveLength(2);
    expect(screen.getByText('월~금 09:00~18:00')).toBeTruthy();
    expect(screen.getByText('매주 일요일')).toBeTruthy();
    expect(screen.getByText('전체 12개')).toBeTruthy();
    expect(screen.getByText('접수중 5개')).toBeTruthy();
    expect(screen.getByText('마감임박 2개')).toBeTruthy();
    expect(screen.getByRole('link', { name: '홈페이지' }).getAttribute('href')).toBe(
      'https://example.com/branch',
    );
    expect(screen.getByRole('link', { name: '전화' }).getAttribute('href')).toBe(
      'tel:0212345678',
    );
  });

  it('allows the information panel to close without closing the picker', () => {
    renderPicker();
    fireEvent.click(screen.getByRole('button', { name: /테스트 지점/ }));

    fireEvent.click(screen.getByRole('button', { name: '지점 정보 닫기' }));

    expect(screen.queryByRole('complementary', { name: '선택 지점 정보' })).toBeNull();
    expect(screen.getByRole('dialog', { name: '위치·지점 선택' })).toBeTruthy();
  });
});
