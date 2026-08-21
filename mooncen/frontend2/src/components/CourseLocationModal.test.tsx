import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ClassItem } from '../data/mockData';
import { loadKakaoMaps } from '../utils/kakaoMaps';
import CourseLocationModal from './CourseLocationModal';

vi.mock('../utils/kakaoMaps', async () => {
  const actual = await vi.importActual<typeof import('../utils/kakaoMaps')>('../utils/kakaoMaps');
  return { ...actual, loadKakaoMaps: vi.fn() };
});

const item = {
  id: 'course-location',
  title: '테니스 클래스',
  center: '롯데문화센터 동탄점',
  venueName: '롯데문화센터 동탄점',
  venueAddress: '경기도 화성시 동탄중앙로 200',
  distanceKm: 2.4,
  branch: {
    id: 'branch-location',
    name: '동탄점',
    provider: 'LOTTE',
    address: '경기도 화성시 동탄중앙로 200',
    phone: '031-123-4567',
    lat: 37.2,
    lon: 127.1,
    website_url: 'https://example.com/branch',
    operating_hours: '10:00~20:00',
  },
} as ClassItem;

beforeEach(() => {
  vi.stubEnv('VITE_KAKAO_MAPS_JAVASCRIPT_KEY', '');
  vi.mocked(loadKakaoMaps).mockReset();
});
afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

describe('CourseLocationModal', () => {
  it('shows the selected course location and map actions', () => {
    render(<CourseLocationModal item={item} onClose={vi.fn()} />);

    expect(screen.getByRole('dialog', { name: '롯데문화센터 동탄점' })).toBeTruthy();
    expect(screen.getByText('경기도 화성시 동탄중앙로 200')).toBeTruthy();
    expect(screen.getByText('기준 위치에서 2.4km')).toBeTruthy();
    expect(screen.getByText('031-123-4567')).toBeTruthy();
    expect(screen.getByTitle('롯데문화센터 동탄점 카카오 지도').getAttribute('href')).toContain(
      'https://map.kakao.com/link/map/',
    );
    expect(screen.getByRole('link', { name: '길찾기' }).getAttribute('href')).toContain(
      'https://map.kakao.com/link/to/',
    );
    expect(screen.getByRole('link', { name: '전화' }).getAttribute('href')).toBe('tel:0311234567');
    expect(screen.getByRole('link', { name: '홈페이지' }).getAttribute('href')).toBe(
      'https://example.com/branch',
    );
  });

  it('closes from the close button', () => {
    const onClose = vi.fn();
    render(<CourseLocationModal item={item} onClose={onClose} />);

    fireEvent.click(screen.getByRole('button', { name: '위치 팝업 닫기' }));

    expect(onClose).toHaveBeenCalledOnce();
  });

  it('turns an address-only location into a Kakao directions link', async () => {
    vi.stubEnv('VITE_KAKAO_MAPS_JAVASCRIPT_KEY', 'test-javascript-key');
    const fakeMaps = {
      Map: class {
        addControl() {}
        relayout() {}
        setCenter() {}
      },
      LatLng: class {},
      Marker: class {
        setMap() {}
        setPosition() {}
        setTitle() {}
      },
      ZoomControl: class {},
      ControlPosition: { RIGHT: 1 },
      services: {
        Status: { OK: 'OK' },
        Geocoder: class {
          addressSearch(
            _address: string,
            callback: (result: KakaoMapsGeocoderResult[], status: string) => void,
          ) {
            callback([{ x: '127.25', y: '37.45' }], 'OK');
          }
        },
      },
    } as unknown as KakaoMapsNamespace;
    vi.mocked(loadKakaoMaps).mockResolvedValue(fakeMaps);
    const addressOnlyItem = {
      ...item,
      id: 'address-only-location',
      branch: { ...item.branch, lat: null, lon: null },
    } as ClassItem;

    render(<CourseLocationModal item={addressOnlyItem} onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByRole('link', { name: '길찾기' }).getAttribute('href')).toContain(
        'https://map.kakao.com/link/to/',
      );
    });
    expect(screen.getByRole('link', { name: '길찾기' }).getAttribute('href')).toContain(
      ',37.45,127.25',
    );
  });
});
