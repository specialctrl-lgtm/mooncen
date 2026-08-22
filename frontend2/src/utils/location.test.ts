import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Branch } from '../api';
import {
  clearCachedLocation,
  DEFAULT_MAP_DIAMETER_KM,
  diameterToRadiusKm,
  filterBranchesWithinRadius,
  distanceKm,
  MAP_DIAMETER_OPTIONS_KM,
  formatDistanceLabel,
  readCachedLocation,
  writeCachedLocation,
} from './location';

const LOCATION_CACHE_KEY = 'mooncen.lastLocation';

describe('location helpers', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-07-10T12:00:00+09:00'));
    window.sessionStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
    window.sessionStorage.clear();
  });

  it('stores a short-lived, rounded location and clears it', () => {
    writeCachedLocation({ lat: 37.56649, lon: 126.97844, label: '현재 위치', accuracy: 15, detected: true });

    expect(readCachedLocation()).toMatchObject({
      lat: 37.566,
      lon: 126.978,
      label: '현재 위치 근처',
      detected: true,
    });

    clearCachedLocation();
    expect(readCachedLocation()).toBeNull();
  });

  it('rejects an expired cached location', () => {
    window.sessionStorage.setItem(LOCATION_CACHE_KEY, JSON.stringify({
      lat: 37.5,
      lon: 127,
      savedAt: Date.now() - 31 * 60 * 1000,
    }));

    expect(readCachedLocation()).toBeNull();
  });

  it('calculates distance and formats meter/kilometer labels', () => {
    const branch = { id: 'branch', name: '지점', provider: 'LOTTE', lat: 37.5665, lon: 126.978 } as Branch;

    expect(distanceKm({ lat: 37.5665, lon: 126.978, label: '기준' }, branch)).toBe(0);
    expect(formatDistanceLabel(0.42)).toBe('420m');
    expect(formatDistanceLabel(3.25)).toBe('3.3km');
  });

  it('converts the supported 5, 10, and 20 kilometer diameters to radii', () => {
    expect(MAP_DIAMETER_OPTIONS_KM).toEqual([5, 10, 20]);
    expect(DEFAULT_MAP_DIAMETER_KM).toBe(20);
    expect(MAP_DIAMETER_OPTIONS_KM.map(diameterToRadiusKm)).toEqual([2.5, 5, 10]);
  });

  it('keeps only branches inside half of the selected diameter', () => {
    const origin = { lat: 37.5665, lon: 126.978, label: '기준 위치' };
    const branches = [
      { id: 'near', name: '약 2km', provider: 'TEST', lat: 37.5845, lon: 126.978 },
      { id: 'middle', name: '약 4km', provider: 'TEST', lat: 37.6015, lon: 126.978 },
      { id: 'wide', name: '약 9km', provider: 'TEST', lat: 37.6465, lon: 126.978 },
      { id: 'outside', name: '약 11km', provider: 'TEST', lat: 37.6655, lon: 126.978 },
      { id: 'unknown', name: '좌표 없음', provider: 'TEST' },
    ] as Branch[];

    expect(filterBranchesWithinRadius(origin, branches, diameterToRadiusKm(5)).map((branch) => branch.id)).toEqual(['near']);
    expect(filterBranchesWithinRadius(origin, branches, diameterToRadiusKm(10)).map((branch) => branch.id)).toEqual([
      'near',
      'middle',
    ]);
    expect(filterBranchesWithinRadius(origin, branches, diameterToRadiusKm(20)).map((branch) => branch.id)).toEqual([
      'near',
      'middle',
      'wide',
    ]);
  });
});
