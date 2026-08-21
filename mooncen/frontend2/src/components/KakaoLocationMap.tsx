import { useEffect, useRef, useState } from 'react';
import { runtimeConfig } from '../runtimeConfig';
import { kakaoMapLink, loadKakaoMaps } from '../utils/kakaoMaps';

type KakaoLocationMapProps = {
  name: string;
  address?: string | null;
  lat?: number | null;
  lon?: number | null;
  onCoordinatesResolved?: (lat: number, lon: number) => void;
};

function finiteCoordinate(value?: number | null): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

export default function KakaoLocationMap({
  name,
  address,
  lat,
  lon,
  onCoordinatesResolved,
}: KakaoLocationMapProps) {
  const apiKey = runtimeConfig.kakaoMapsJavaScriptKey;
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<KakaoMapsMap | null>(null);
  const markerRef = useRef<KakaoMapsMarker | null>(null);
  const observerRef = useRef<ResizeObserver | null>(null);
  const [maps, setMaps] = useState<KakaoMapsNamespace | null>(null);
  const [geocodedPosition, setGeocodedPosition] = useState<{
    query: string;
    lat: number;
    lon: number;
  } | null>(null);
  const hasCoordinates = finiteCoordinate(lat) && finiteCoordinate(lon);
  const addressQuery = address?.trim() || '';
  const resolvedLat = hasCoordinates
    ? lat
    : geocodedPosition?.query === addressQuery ? geocodedPosition.lat : null;
  const resolvedLon = hasCoordinates
    ? lon
    : geocodedPosition?.query === addressQuery ? geocodedPosition.lon : null;
  const hasResolvedCoordinates = finiteCoordinate(resolvedLat) && finiteCoordinate(resolvedLon);
  const externalMapUrl = kakaoMapLink({ name, address, lat, lon });

  useEffect(() => {
    if (!apiKey || (!hasCoordinates && !addressQuery)) return undefined;
    let active = true;
    loadKakaoMaps(apiKey).then(
      (loadedMaps) => {
        if (active) setMaps(loadedMaps);
      },
      () => {
        // The external Kakao Map link remains available as the safe fallback.
      },
    );
    return () => {
      active = false;
    };
  }, [addressQuery, apiKey, hasCoordinates]);

  useEffect(() => {
    if (!maps || hasCoordinates || !addressQuery) return undefined;
    let active = true;
    const geocoder = new maps.services.Geocoder();
    geocoder.addressSearch(addressQuery, (result, status) => {
      const first = result[0];
      const nextLat = Number(first?.y);
      const nextLon = Number(first?.x);
      if (active && status === maps.services.Status.OK && Number.isFinite(nextLat) && Number.isFinite(nextLon)) {
        setGeocodedPosition({ query: addressQuery, lat: nextLat, lon: nextLon });
        onCoordinatesResolved?.(nextLat, nextLon);
      }
    });
    return () => {
      active = false;
    };
  }, [addressQuery, hasCoordinates, maps, onCoordinatesResolved]);

  useEffect(() => {
    const container = containerRef.current;
    if (!maps || !container || !hasResolvedCoordinates) return;
    const position = new maps.LatLng(resolvedLat, resolvedLon);

    if (!mapRef.current) {
      const map = new maps.Map(container, {
        center: position,
        level: 4,
        draggable: true,
        scrollwheel: true,
        disableDoubleClick: false,
        disableDoubleClickZoom: false,
        keyboardShortcuts: true,
      });
      map.addControl(new maps.ZoomControl(), maps.ControlPosition.RIGHT);
      mapRef.current = map;
      if (typeof ResizeObserver !== 'undefined') {
        observerRef.current = new ResizeObserver(() => map.relayout());
        observerRef.current.observe(container);
      }
    } else {
      mapRef.current.setCenter(position);
      mapRef.current.relayout();
    }

    if (!markerRef.current) {
      markerRef.current = new maps.Marker({
        map: mapRef.current,
        position,
        title: name,
      });
    } else {
      markerRef.current.setPosition(position);
      markerRef.current.setTitle(name);
    }
  }, [hasResolvedCoordinates, maps, name, resolvedLat, resolvedLon]);

  useEffect(() => () => {
    observerRef.current?.disconnect();
    markerRef.current?.setMap(null);
  }, []);

  const showFallback = !apiKey || !maps || !hasResolvedCoordinates;

  return (
    <div className="kakao-location-map-shell">
      <div
        ref={containerRef}
        className="kakao-location-map-canvas"
        title={showFallback ? undefined : `${name} 카카오 지도`}
        aria-hidden={showFallback ? 'true' : undefined}
      />
      {showFallback && (
        <a
          className="kakao-location-map-fallback"
          href={externalMapUrl}
          target="_blank"
          rel="noopener noreferrer"
          title={`${name} 카카오 지도`}
        >
          <strong>{name}</strong>
          <span>카카오맵에서 위치를 확인하세요.</span>
          <em>카카오맵에서 보기</em>
        </a>
      )}
    </div>
  );
}
