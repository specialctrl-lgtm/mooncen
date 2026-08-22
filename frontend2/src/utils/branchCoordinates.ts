import type { Branch } from '../api';

export type BranchCoordinates = {
  lat: number;
  lon: number;
};

function finiteCoordinate(value: unknown) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value !== 'string' || !value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * API coordinates normally arrive as numbers, but older deployments and cached
 * responses can contain decimal strings. Validate them at the rendering
 * boundary so an invalid value can never create a Kakao marker at NaN/NaN.
 */
export function branchCoordinates(
  branch: Pick<Branch, 'lat' | 'lon'> | null | undefined,
): BranchCoordinates | null {
  if (!branch) return null;
  const lat = finiteCoordinate(branch.lat);
  const lon = finiteCoordinate(branch.lon);
  if (lat == null || lon == null) return null;
  if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return null;
  return { lat, lon };
}

export function hasValidBranchCoordinates(
  branch: Pick<Branch, 'lat' | 'lon'> | null | undefined,
) {
  return branchCoordinates(branch) !== null;
}
