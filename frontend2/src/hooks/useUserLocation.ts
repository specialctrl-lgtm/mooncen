import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  clearCachedLocation,
  DEFAULT_LOCATION,
  readCachedLocation,
  writeCachedLocation,
  type UserLocation,
} from '../utils/location';

type UseUserLocationOptions = {
  onContextReset: () => void;
  onNotice: (message: string) => void;
};

const BALANCED_LOCATION_OPTIONS: PositionOptions = {
  enableHighAccuracy: false,
  timeout: 12_000,
  maximumAge: 1000 * 60 * 30,
};

const PRECISE_LOCATION_OPTIONS: PositionOptions = {
  enableHighAccuracy: true,
  timeout: 20_000,
  maximumAge: 0,
};

const DEV_NETWORK_LOCATION_URL = 'https://ipapi.co/json/';
const DEV_NETWORK_FALLBACK_DELAY_MS = 4_000;
const NETWORK_LOCATION_TIMEOUT_MS = 5_000;

type NetworkLocationResponse = {
  error?: boolean;
  reason?: string;
  city?: string;
  region?: string;
  country_code?: string;
  latitude?: number;
  longitude?: number;
};

function isValidCoordinate(lat: number, lon: number) {
  return Number.isFinite(lat) && Number.isFinite(lon)
    && lat >= -90 && lat <= 90
    && lon >= -180 && lon <= 180;
}

async function fetchNetworkLocation() {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), NETWORK_LOCATION_TIMEOUT_MS);

  try {
    const response = await fetch(DEV_NETWORK_LOCATION_URL, {
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`network location HTTP ${response.status}`);

    const payload = await response.json() as NetworkLocationResponse;
    const lat = Number(payload.latitude);
    const lon = Number(payload.longitude);
    if (payload.error || !isValidCoordinate(lat, lon)) {
      throw new Error(payload.reason || 'invalid network location');
    }

    const area = [payload.city, payload.region].filter(Boolean).join(', ');
    return {
      lat,
      lon,
      label: area ? `${area} 네트워크 위치` : '네트워크 기준 위치',
    };
  } finally {
    window.clearTimeout(timeoutId);
  }
}

function locationErrorMessage(geoError: GeolocationPositionError) {
  if (geoError.code === 1) {
    return '이 사이트의 위치 권한이 차단되어 있습니다. 주소창의 사이트 설정에서 위치를 허용한 뒤 다시 시도해 주세요.';
  }
  if (geoError.code === 3) {
    return '위치 확인이 계속 지연되고 있습니다. 기기 위치 서비스를 켠 뒤 다시 시도해 주세요.';
  }
  return '기기에서 위치 정보를 확인할 수 없습니다. 위치 서비스와 네트워크 상태를 확인한 뒤 다시 시도해 주세요.';
}

export function useUserLocation({ onContextReset, onNotice }: UseUserLocationOptions) {
  const cachedInitialLocation = useMemo(() => readCachedLocation(), []);
  const [userLocation, setUserLocation] = useState<UserLocation>(cachedInitialLocation ?? DEFAULT_LOCATION);
  const [usingCurrentLocation, setUsingCurrentLocation] = useState(Boolean(cachedInitialLocation));
  const [locating, setLocating] = useState(false);
  const [locationReady, setLocationReady] = useState(true);
  const [locationError, setLocationError] = useState<string | null>(null);
  const locationRequestIdRef = useRef(0);
  const automaticRequestStartedRef = useRef(false);

  const requestCurrentLocation = useCallback(() => {
    const requestId = locationRequestIdRef.current + 1;
    locationRequestIdRef.current = requestId;
    let settled = false;
    let networkFallbackTimer: number | null = null;
    const isCurrentRequest = () => locationRequestIdRef.current === requestId && !settled;
    const clearNetworkFallbackTimer = () => {
      if (networkFallbackTimer == null) return;
      window.clearTimeout(networkFallbackTimer);
      networkFallbackTimer = null;
    };

    setLocationError(null);
    setLocating(true);

    const applyDetectedLocation = (
      lat: number,
      lon: number,
      accuracy?: number,
      label = '현재 위치 기준',
      source: UserLocation['source'] = 'device',
    ) => {
      if (!isCurrentRequest()) return;
      settled = true;
      clearNetworkFallbackTimer();
      const detectedLocation = {
        lat,
        lon,
        label,
        accuracy,
        detected: true,
        source,
      };
      writeCachedLocation(detectedLocation);
      setUserLocation(detectedLocation);
      setLocationError(null);
      setUsingCurrentLocation(true);
      setLocationReady(true);
      setLocating(false);
      onContextReset();
      onNotice(
        source === 'network'
          ? `${label}를 사용합니다. 지도에서 위치를 조정할 수 있습니다.`
          : '현재 위치 기준으로 지도를 이동했습니다.',
      );
    };

    const requestNetworkFallback = async () => {
      if (!import.meta.env.DEV || !isCurrentRequest()) return false;
      try {
        const location = await fetchNetworkLocation();
        if (!isCurrentRequest()) return false;
        applyDetectedLocation(location.lat, location.lon, undefined, location.label, 'network');
        return true;
      } catch {
        return false;
      }
    };

    const handleSuccess = (position: GeolocationPosition) => {
      applyDetectedLocation(
        position.coords.latitude,
        position.coords.longitude,
        position.coords.accuracy,
      );
    };

    const handleFinalError = (geoError: GeolocationPositionError) => {
      if (!isCurrentRequest()) return;
      settled = true;
      clearNetworkFallbackTimer();
      setLocationError(locationErrorMessage(geoError));
      setLocating(false);
    };

    const handleUnavailableBrowserLocation = (message: string) => {
      if (!import.meta.env.DEV) {
        settled = true;
        setLocationError(message);
        setLocating(false);
        return;
      }
      void requestNetworkFallback().then((usedFallback) => {
        if (usedFallback || !isCurrentRequest()) return;
        settled = true;
        setLocationError(message);
        setLocating(false);
      });
    };

    if (!window.isSecureContext) {
      handleUnavailableBrowserLocation('현재 위치는 HTTPS 또는 localhost에서만 사용할 수 있습니다.');
      return;
    }

    if (!navigator.geolocation) {
      handleUnavailableBrowserLocation('이 브라우저에서는 현재 위치를 사용할 수 없습니다.');
      return;
    }

    const requestPreciseLocation = () => {
      if (!isCurrentRequest()) return;
      navigator.geolocation.getCurrentPosition(
        handleSuccess,
        handleFinalError,
        PRECISE_LOCATION_OPTIONS,
      );
    };

    const handleBalancedError = (geoError: GeolocationPositionError) => {
      if (!isCurrentRequest()) return;
      if (geoError.code === 1) {
        handleFinalError(geoError);
        return;
      }
      requestPreciseLocation();
    };

    const startLocationRequest = () => {
      if (!isCurrentRequest()) return;
      navigator.geolocation.getCurrentPosition(
        handleSuccess,
        handleBalancedError,
        BALANCED_LOCATION_OPTIONS,
      );
    };

    // Request geolocation directly. Waiting for the Permissions API first can
    // prevent the prompt from appearing in mobile browsers and embedded webviews.
    if (import.meta.env.DEV) {
      networkFallbackTimer = window.setTimeout(() => {
        void requestNetworkFallback();
      }, DEV_NETWORK_FALLBACK_DELAY_MS);
    }
    startLocationRequest();
  }, [onContextReset, onNotice]);

  useEffect(() => {
    if (cachedInitialLocation || automaticRequestStartedRef.current) return;
    automaticRequestStartedRef.current = true;
    requestCurrentLocation();
  }, [cachedInitialLocation, requestCurrentLocation]);

  const stopUsingCurrentLocation = useCallback(() => {
    locationRequestIdRef.current += 1;
    clearCachedLocation();
    setUserLocation(DEFAULT_LOCATION);
    setUsingCurrentLocation(false);
    setLocating(false);
    setLocationError(null);
    onContextReset();
    onNotice('저장된 위치를 지우고 기본 지역으로 돌아왔습니다.');
  }, [onContextReset, onNotice]);

  return {
    userLocation,
    usingCurrentLocation,
    locating,
    locationReady,
    locationError,
    setLocationError,
    requestCurrentLocation,
    stopUsingCurrentLocation,
  };
}
