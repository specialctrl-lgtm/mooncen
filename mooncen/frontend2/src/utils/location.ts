import type { Branch } from '../api';
import { branchCoordinates } from './branchCoordinates';

export type UserLocation = {
  lat: number;
  lon: number;
  label: string;
  accuracy?: number;
  detected?: boolean;
  source?: 'device' | 'network';
};

export const MAP_DIAMETER_OPTIONS_KM = [5, 10, 20] as const;
export type MapDiameterKm = (typeof MAP_DIAMETER_OPTIONS_KM)[number];
export const DEFAULT_MAP_DIAMETER_KM: MapDiameterKm = MAP_DIAMETER_OPTIONS_KM[2];

export function diameterToRadiusKm(diameterKm: number) {
  return diameterKm / 2;
}

export const DEFAULT_LOCATION: UserLocation = {
  lat: 37.5665,
  lon: 126.978,
  label: '지역 기준 보기',
  detected: false,
};

const LOCATION_CACHE_KEY = 'mooncen.lastLocation';
const LOCATION_CACHE_MAX_AGE_MS = 1000 * 60 * 30;

export function readCachedLocation() {
  try {
    const raw = window.sessionStorage.getItem(LOCATION_CACHE_KEY);
    if (!raw) return null;
    const cached = JSON.parse(raw) as UserLocation & { savedAt?: number };
    if (
      typeof cached.lat !== 'number' ||
      typeof cached.lon !== 'number' ||
      typeof cached.savedAt !== 'number' ||
      Date.now() - cached.savedAt > LOCATION_CACHE_MAX_AGE_MS
    ) {
      return null;
    }
    return {
      lat: cached.lat,
      lon: cached.lon,
      label: cached.label || '최근 위치 기준',
      accuracy: cached.accuracy,
      detected: true,
      source: cached.source === 'network' ? 'network' : 'device',
    } satisfies UserLocation;
  } catch {
    return null;
  }
}

export function writeCachedLocation(location: UserLocation) {
  try {
    window.sessionStorage.setItem(LOCATION_CACHE_KEY, JSON.stringify({
      lat: Number(location.lat.toFixed(3)),
      lon: Number(location.lon.toFixed(3)),
      label: location.source === 'network' ? location.label : '현재 위치 근처',
      accuracy: location.accuracy,
      detected: true,
      source: location.source ?? 'device',
      savedAt: Date.now(),
    }));
  } catch {
    // Live geolocation remains usable when storage is unavailable.
  }
}

export function clearCachedLocation() {
  try {
    window.sessionStorage.removeItem(LOCATION_CACHE_KEY);
  } catch {
    // Storage cleanup is best-effort.
  }
}

export function distanceKm(from: UserLocation, branch: Branch) {
  const coordinates = branchCoordinates(branch);
  if (!coordinates) return Number.POSITIVE_INFINITY;
  const toRadians = (value: number) => (value * Math.PI) / 180;
  const earthRadiusKm = 6371;
  const dLat = toRadians(coordinates.lat - from.lat);
  const dLon = toRadians(coordinates.lon - from.lon);
  const lat1 = toRadians(from.lat);
  const lat2 = toRadians(coordinates.lat);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 2 * earthRadiusKm * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

export function filterBranchesWithinRadius(
  from: UserLocation,
  branches: Branch[],
  radiusKm: number,
) {
  return branches.filter((branch) => distanceKm(from, branch) <= radiusKm);
}

export function formatDistanceLabel(distance: number) {
  if (!Number.isFinite(distance)) return '';
  if (distance < 1) return `${Math.round(distance * 1000).toLocaleString('ko-KR')}m`;
  return `${distance.toLocaleString('ko-KR', { maximumFractionDigits: distance < 10 ? 1 : 0 })}km`;
}
