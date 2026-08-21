import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DEFAULT_LOCATION, writeCachedLocation } from '../utils/location';
import { useUserLocation } from './useUserLocation';

function position(latitude = 37.5665, longitude = 126.978, accuracy = 80) {
  return {
    coords: {
      latitude,
      longitude,
      accuracy,
      altitude: null,
      altitudeAccuracy: null,
      heading: null,
      speed: null,
    },
    timestamp: Date.now(),
  } as GeolocationPosition;
}

function geolocationError(code: 1 | 2 | 3) {
  return {
    code,
    message: 'test error',
    PERMISSION_DENIED: 1,
    POSITION_UNAVAILABLE: 2,
    TIMEOUT: 3,
  } as GeolocationPositionError;
}

function installLocationBrowser(permissionState: PermissionState = 'granted') {
  const getCurrentPosition = vi.fn();
  const query = vi.fn().mockResolvedValue({ state: permissionState } as PermissionStatus);
  Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true });
  Object.defineProperty(navigator, 'geolocation', {
    configurable: true,
    value: { getCurrentPosition },
  });
  Object.defineProperty(navigator, 'permissions', {
    configurable: true,
    value: { query },
  });
  return { getCurrentPosition, query };
}

function renderLocationHook() {
  const onContextReset = vi.fn();
  const onNotice = vi.fn();
  const hook = renderHook(() => useUserLocation({ onContextReset, onNotice }));
  return { ...hook, onContextReset, onNotice };
}

describe('useUserLocation', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('uses a balanced cached-capable request before high accuracy', async () => {
    const { getCurrentPosition } = installLocationBrowser();
    const { result, onContextReset, onNotice } = renderLocationHook();

    await waitFor(() => expect(getCurrentPosition).toHaveBeenCalledTimes(1));

    const firstOptions = getCurrentPosition.mock.calls[0][2] as PositionOptions;
    expect(firstOptions).toEqual({
      enableHighAccuracy: false,
      timeout: 12_000,
      maximumAge: 1000 * 60 * 30,
    });

    act(() => (getCurrentPosition.mock.calls[0][0] as PositionCallback)(position()));

    expect(result.current.userLocation).toMatchObject({
      lat: 37.5665,
      lon: 126.978,
      accuracy: 80,
      detected: true,
    });
    expect(result.current.locating).toBe(false);
    expect(result.current.locationError).toBeNull();
    expect(onContextReset).toHaveBeenCalledOnce();
    expect(onNotice).toHaveBeenCalledWith('현재 위치 기준으로 지도를 이동했습니다.');
  });

  it('retries once with high accuracy after the balanced request times out', async () => {
    const { getCurrentPosition } = installLocationBrowser();
    const { result } = renderLocationHook();

    await waitFor(() => expect(getCurrentPosition).toHaveBeenCalledTimes(1));
    act(() => (getCurrentPosition.mock.calls[0][1] as PositionErrorCallback)(geolocationError(3)));

    expect(getCurrentPosition).toHaveBeenCalledTimes(2);
    expect(getCurrentPosition.mock.calls[1][2]).toEqual({
      enableHighAccuracy: true,
      timeout: 20_000,
      maximumAge: 0,
    });
    expect(result.current.locationError).toBeNull();
    expect(result.current.locating).toBe(true);

    act(() => (getCurrentPosition.mock.calls[1][0] as PositionCallback)(position(37.5, 127, 15)));
    expect(result.current.userLocation).toMatchObject({ lat: 37.5, lon: 127, accuracy: 15 });
    expect(result.current.locating).toBe(false);
  });

  it('shows a timeout only after both location attempts fail', async () => {
    const { getCurrentPosition } = installLocationBrowser();
    const { result } = renderLocationHook();

    await waitFor(() => expect(getCurrentPosition).toHaveBeenCalledTimes(1));
    act(() => (getCurrentPosition.mock.calls[0][1] as PositionErrorCallback)(geolocationError(3)));
    act(() => (getCurrentPosition.mock.calls[1][1] as PositionErrorCallback)(geolocationError(3)));

    expect(result.current.locating).toBe(false);
    expect(result.current.locationError).toBe(
      '위치 확인이 계속 지연되고 있습니다. 기기 위치 서비스를 켠 뒤 다시 시도해 주세요.',
    );
  });

  it('reports a denied browser geolocation request', () => {
    const { getCurrentPosition, query } = installLocationBrowser('denied');
    const { result } = renderLocationHook();

    expect(getCurrentPosition).toHaveBeenCalledOnce();
    expect(query).not.toHaveBeenCalled();

    act(() => (getCurrentPosition.mock.calls[0][1] as PositionErrorCallback)(geolocationError(1)));

    expect(result.current.locationError).toContain('위치 권한이 차단');
    expect(result.current.locating).toBe(false);
  });

  it('uses an approximate network location when device lookup stalls in development', async () => {
    vi.useFakeTimers();
    const { getCurrentPosition } = installLocationBrowser();
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        city: 'Anyang-si',
        region: 'Gyeonggi-do',
        country_code: 'KR',
        latitude: 37.3811,
        longitude: 126.9296,
      }),
    } as Response);
    vi.stubGlobal('fetch', fetchMock);
    const { result, onNotice } = renderLocationHook();

    expect(getCurrentPosition).toHaveBeenCalledOnce();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_000);
    });

    expect(fetchMock).toHaveBeenCalledWith(
      'https://ipapi.co/json/',
      expect.objectContaining({ headers: { Accept: 'application/json' } }),
    );
    expect(result.current.userLocation).toMatchObject({
      lat: 37.3811,
      lon: 126.9296,
      label: 'Anyang-si, Gyeonggi-do 네트워크 위치',
      source: 'network',
      detected: true,
    });
    expect(result.current.locating).toBe(false);
    expect(onNotice).toHaveBeenCalledWith(
      'Anyang-si, Gyeonggi-do 네트워크 위치를 사용합니다. 지도에서 위치를 조정할 수 있습니다.',
    );
  });

  it('ignores a late callback from an older request', async () => {
    const { getCurrentPosition } = installLocationBrowser();
    const { result, onNotice } = renderLocationHook();

    await waitFor(() => expect(getCurrentPosition).toHaveBeenCalledTimes(1));
    act(() => result.current.requestCurrentLocation());
    await waitFor(() => expect(getCurrentPosition).toHaveBeenCalledTimes(2));

    act(() => (getCurrentPosition.mock.calls[0][0] as PositionCallback)(position(35, 128, 20)));
    expect(result.current.userLocation).toEqual(DEFAULT_LOCATION);

    act(() => (getCurrentPosition.mock.calls[1][0] as PositionCallback)(position(37.5, 127, 10)));
    expect(result.current.userLocation).toMatchObject({ lat: 37.5, lon: 127 });
    expect(onNotice).toHaveBeenCalledOnce();
  });

  it('shows the secure-context error when the development fallback is unavailable', async () => {
    const { getCurrentPosition, query } = installLocationBrowser();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network unavailable')));
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: false });
    const { result } = renderLocationHook();

    await waitFor(() => {
      expect(result.current.locationError).toBe(
        '현재 위치는 HTTPS 또는 localhost에서만 사용할 수 있습니다.',
      );
    });
    expect(result.current.locating).toBe(false);
    expect(query).not.toHaveBeenCalled();
    expect(getCurrentPosition).not.toHaveBeenCalled();
  });

  it('uses a recent cached location without requesting geolocation again', () => {
    writeCachedLocation({
      lat: 37.394,
      lon: 126.956,
      label: '현재 위치 기준',
      detected: true,
      source: 'device',
    });
    const { getCurrentPosition } = installLocationBrowser();
    const { result } = renderLocationHook();

    expect(getCurrentPosition).not.toHaveBeenCalled();
    expect(result.current.usingCurrentLocation).toBe(true);
    expect(result.current.userLocation).toMatchObject({
      lat: 37.394,
      lon: 126.956,
      detected: true,
      source: 'device',
    });
  });
});
