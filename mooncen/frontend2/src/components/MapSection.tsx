import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import type { Branch } from '../api';
import { runtimeConfig } from '../runtimeConfig';
import { loadKakaoMaps } from '../utils/kakaoMaps';
import {
  diameterToRadiusKm,
  type MapDiameterKm,
  type UserLocation,
} from '../utils/location';
import { branchCoordinates } from '../utils/branchCoordinates';
import { mapBranchCourseCount, selectMapMarkerBranches } from '../utils/mapBranches';
import CenterMapMarker from './CenterMapMarker';

type MapSectionProps = {
  branches: Branch[];
  providerFilters: string[];
  categoryFilters: string[];
  categoryFilterActive?: boolean;
  mapMode: 'provider' | 'education' | 'experience';
  branchCourseCounts?: Record<string, number>;
  branchOpenCounts?: Record<string, number>;
  branchUrgentCounts?: Record<string, number>;
  favoriteBranchIds?: string[];
  userLocation: UserLocation;
  myLocation?: UserLocation | null;
  locationError: string | null;
  selectedBranch: Branch | null;
  selectedBranchIds?: string[];
  hoveredBranchId?: string | null;
  viewDiameterKm: MapDiameterKm;
  debugMode: boolean;
  onBranchSelect: (branch: Branch | null) => void;
  onBranchHover?: (branchId: string | null) => void;
  onMapCenterChange: (lat: number, lon: number) => void;
  onVisibleBranchIdsChange: (branchIds: string[]) => void;
};

type Coordinate = {
  lat: number;
  lng: number;
};

function viewportDistanceKm(a: Coordinate, b: Coordinate) {
  const toRad = (value: number) => (value * Math.PI) / 180;
  const earthKm = 6371;
  const dLat = toRad(b.lat - a.lat);
  const dLng = toRad(b.lng - a.lng);
  const lat1 = toRad(a.lat);
  const lat2 = toRad(b.lat);
  const h =
    Math.sin(dLat / 2) ** 2
    + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2;
  return earthKm * 2 * Math.atan2(Math.sqrt(h), Math.sqrt(1 - h));
}

function myLocationMarkerUrl() {
  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 40 40">
      <circle cx="20" cy="20" r="17" fill="#2563EB" fill-opacity="0.18"/>
      <circle cx="20" cy="20" r="10" fill="#2563EB" stroke="#FFFFFF" stroke-width="4"/>
      <circle cx="20" cy="20" r="3.5" fill="#FFFFFF"/>
    </svg>
  `;
  return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`;
}

const cultureLegendItems = [
  { key: 'homeplus', label: '홈플러스', color: '#E11D2E' },
  { key: 'emart', label: '이마트', color: '#F59E0B' },
  { key: 'lotte', label: '롯데', color: '#FB4055' },
  { key: 'ak', label: 'AK PLAZA', color: '#1D5FAE' },
  { key: 'hyundai', label: '현대', color: '#047857' },
  { key: 'shinsegae', label: '신세계', color: '#0F9F95' },
  { key: 'galleria', label: '갤러리아', color: '#7C3AED' },
];

const educationLegendItems = [
  { key: 'public', label: '공공기관', color: '#4CAF3D' },
  { key: 'library', label: '도서관 강좌', color: '#28B9B0' },
  { key: 'youth', label: '청소년센터', color: '#8B42C7' },
];

const experienceLegendItems = [
  { key: 'library', label: '도서관', color: '#28B9B0' },
  { key: 'museum', label: '박물관', color: '#F59E0B' },
  { key: 'science', label: '과학관', color: '#1D9BF0' },
  { key: 'experience', label: '체험시설', color: '#14B8A6' },
];

function legendItemsForMode(mapMode: MapSectionProps['mapMode']) {
  if (mapMode === 'provider') return cultureLegendItems;
  if (mapMode === 'education') return educationLegendItems;
  return experienceLegendItems;
}

function MapMarkerLegend({ mapMode }: { mapMode: MapSectionProps['mapMode'] }) {
  return (
    <div className="map-marker-legend" aria-label="지도 마커 범례">
      {legendItemsForMode(mapMode).map((item) => (
        <span className="legend-item" key={item.key}>
          <i className="legend-dot" style={{ '--legend-color': item.color } as CSSProperties} />
          {item.label}
        </span>
      ))}
    </div>
  );
}

function MapFallback({
  mapMode,
  locationError,
  error,
}: {
  mapMode: MapSectionProps['mapMode'];
  locationError?: string | null;
  error?: string | null;
}) {
  return (
    <section
      className="map-card google-map-card kakao-map-card google-map-card-fallback"
      aria-label={error ? '카카오 지도 오류' : '카카오 지도 설정'}
    >
      <div className={`google-map-fallback${error ? ' error' : ''}`}>
        <strong>{error ? '카카오 지도를 불러오지 못했습니다.' : '카카오 지도 설정이 필요합니다.'}</strong>
        <span>
          {error || '런타임 공개 설정에 카카오 지도 JavaScript 키를 설정해주세요.'}
        </span>
        {locationError && <em>{locationError}</em>}
      </div>
      <MapMarkerLegend mapMode={mapMode} />
    </section>
  );
}

export default function MapSection(props: MapSectionProps) {
  const apiKey = runtimeConfig.kakaoMapsJavaScriptKey;
  const [maps, setMaps] = useState<KakaoMapsNamespace | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    if (!apiKey) return undefined;
    let active = true;
    loadKakaoMaps(apiKey).then(
      (loadedMaps) => {
        if (active) setMaps(loadedMaps);
      },
      (error: unknown) => {
        if (active) {
          setLoadError(error instanceof Error ? error.message : '카카오 지도 SDK 로드에 실패했습니다.');
        }
      },
    );
    return () => {
      active = false;
    };
  }, [apiKey]);

  if (!apiKey) {
    return <MapFallback mapMode={props.mapMode} locationError={props.locationError} />;
  }
  if (loadError) {
    return <MapFallback mapMode={props.mapMode} locationError={props.locationError} error={loadError} />;
  }
  if (!maps) {
    return (
      <section
        className="map-card google-map-card kakao-map-card google-map-card-fallback"
        aria-label="카카오 지도 로딩"
      >
        <div className="google-map-fallback">
          <strong>카카오 지도를 불러오는 중입니다.</strong>
        </div>
        <MapMarkerLegend mapMode={props.mapMode} />
      </section>
    );
  }

  return <KakaoMapCanvas {...props} maps={maps} />;
}

function KakaoMapCanvas({
  branches,
  providerFilters,
  categoryFilters,
  categoryFilterActive = true,
  mapMode,
  branchCourseCounts,
  branchUrgentCounts,
  favoriteBranchIds,
  userLocation,
  myLocation = null,
  locationError,
  selectedBranch,
  selectedBranchIds,
  hoveredBranchId,
  viewDiameterKm,
  debugMode,
  onBranchSelect,
  onBranchHover,
  onMapCenterChange,
  onVisibleBranchIdsChange,
  maps,
}: MapSectionProps & { maps: KakaoMapsNamespace }) {
  const mapContainerRef = useRef<HTMLDivElement | null>(null);
  const mapInstanceRef = useRef<KakaoMapsMap | null>(null);
  const [map, setMap] = useState<KakaoMapsMap | null>(null);
  const lastReportedCenterRef = useRef<Coordinate>({
    lat: userLocation.lat,
    lng: userLocation.lon,
  });
  const pendingReportedCenterRef = useRef<Coordinate | null>(null);
  const viewRadiusKm = diameterToRadiusKm(viewDiameterKm);

  const initializeMap = useCallback((container: HTMLDivElement | null) => {
    mapContainerRef.current = container;
    if (!container || mapInstanceRef.current) return;
    const nextMap = new maps.Map(container, {
      center: new maps.LatLng(userLocation.lat, userLocation.lon),
      level: 7,
      draggable: true,
      scrollwheel: true,
      disableDoubleClick: false,
      disableDoubleClickZoom: false,
      keyboardShortcuts: true,
    });
    nextMap.addControl(new maps.ZoomControl(), maps.ControlPosition.RIGHT);
    mapInstanceRef.current = nextMap;
    setMap(nextMap);
  }, [maps, userLocation.lat, userLocation.lon]);

  const providerFilterSet = useMemo(() => new Set(providerFilters), [providerFilters]);
  const categoryFilterSet = useMemo(() => new Set(categoryFilters), [categoryFilters]);
  const favoriteBranchSet = useMemo(() => new Set(favoriteBranchIds || []), [favoriteBranchIds]);
  const selectedBranchSet = useMemo(() => new Set(selectedBranchIds || []), [selectedBranchIds]);

  const getBranchCourseCount = useCallback(
    (branch: Branch) => mapBranchCourseCount(branch, mapMode, branchCourseCounts),
    [branchCourseCounts, mapMode],
  );

  const branchesWithCoords = useMemo(
    () => selectMapMarkerBranches({
      branches,
      userLocation,
      radiusKm: viewRadiusKm,
      providerFilters: [...providerFilterSet],
      categoryFilters: [...categoryFilterSet],
      categoryFilterActive,
      mapMode,
      resultCourseCounts: branchCourseCounts,
    }),
    [branches, branchCourseCounts, categoryFilterActive, categoryFilterSet, mapMode, providerFilterSet, userLocation, viewRadiusKm],
  );

  const fitSearchRadius = useCallback((mapInstance: KakaoMapsMap) => {
    const bounds = new maps.LatLngBounds();
    const latDelta = viewRadiusKm / 111;
    const lngDelta = viewRadiusKm
      / (111 * Math.max(Math.cos((userLocation.lat * Math.PI) / 180), 0.2));
    const mapElement = mapContainerRef.current;
    const fitHorizontalSpan = !mapElement || mapElement.clientWidth >= mapElement.clientHeight;

    if (fitHorizontalSpan) {
      bounds.extend(new maps.LatLng(userLocation.lat, userLocation.lon - lngDelta));
      bounds.extend(new maps.LatLng(userLocation.lat, userLocation.lon + lngDelta));
    } else {
      bounds.extend(new maps.LatLng(userLocation.lat - latDelta, userLocation.lon));
      bounds.extend(new maps.LatLng(userLocation.lat + latDelta, userLocation.lon));
    }
    mapInstance.setBounds(bounds, 24, 24, 24, 24);
  }, [maps, userLocation.lat, userLocation.lon, viewRadiusKm]);

  const reportVisibleBranches = useCallback(() => {
    if (!map) return;
    const bounds = map.getBounds();
    const visibleIds = branchesWithCoords
      .filter((branch) => {
        const coordinates = branchCoordinates(branch);
        return Boolean(coordinates && bounds.contain(new maps.LatLng(coordinates.lat, coordinates.lon)));
      })
      .map((branch) => branch.id);
    onVisibleBranchIdsChange(visibleIds);
  }, [branchesWithCoords, map, maps, onVisibleBranchIdsChange]);

  const reportMapViewport = useCallback(() => {
    if (!map) return;
    const mapCenter = map.getCenter();
    const nextCenter = { lat: mapCenter.getLat(), lng: mapCenter.getLng() };
    reportVisibleBranches();
    const previousCenter = lastReportedCenterRef.current;
    const movedKm = viewportDistanceKm(previousCenter, nextCenter);
    if (movedKm < 0.05) return;

    lastReportedCenterRef.current = nextCenter;
    pendingReportedCenterRef.current = nextCenter;
    onMapCenterChange(nextCenter.lat, nextCenter.lng);
  }, [map, onMapCenterChange, reportVisibleBranches]);

  useEffect(() => {
    if (!map || debugMode) return;
    map.setMinLevel(1);
    map.setMaxLevel(14);
  }, [debugMode, map]);

  useEffect(() => {
    if (!map) return undefined;
    const pendingCenter = pendingReportedCenterRef.current;
    pendingReportedCenterRef.current = null;
    if (pendingCenter) {
      const nextUserCenter = { lat: userLocation.lat, lng: userLocation.lon };
      if (viewportDistanceKm(pendingCenter, nextUserCenter) < 0.05) return undefined;
    }
    fitSearchRadius(map);
    return undefined;
  }, [fitSearchRadius, map, userLocation.lat, userLocation.lon]);

  useEffect(() => {
    if (!map) return undefined;
    const handleIdle = () => reportMapViewport();
    const handleClick = () => onBranchSelect(null);
    maps.event.addListener(map, 'idle', handleIdle);
    maps.event.addListener(map, 'click', handleClick);
    return () => {
      maps.event.removeListener(map, 'idle', handleIdle);
      maps.event.removeListener(map, 'click', handleClick);
    };
  }, [map, maps, onBranchSelect, reportMapViewport]);

  useEffect(() => {
    if (!map) return undefined;
    reportVisibleBranches();
    return undefined;
  }, [map, reportVisibleBranches]);

  useEffect(() => {
    const container = mapContainerRef.current;
    if (!map || !container || typeof ResizeObserver === 'undefined') return undefined;
    let animationFrame = 0;
    const observer = new ResizeObserver(() => {
      map.relayout();
      window.cancelAnimationFrame(animationFrame);
      animationFrame = window.requestAnimationFrame(() => {
        fitSearchRadius(map);
        reportVisibleBranches();
      });
    });
    observer.observe(container);
    return () => {
      window.cancelAnimationFrame(animationFrame);
      observer.disconnect();
    };
  }, [fitSearchRadius, map, reportVisibleBranches]);

  useEffect(() => {
    if (!map) return undefined;
    const circle = new maps.Circle({
      map,
      center: new maps.LatLng(userLocation.lat, userLocation.lon),
      radius: viewRadiusKm * 1000,
      clickable: false,
      fillColor: '#0F9F95',
      fillOpacity: 0.035,
      strokeColor: '#0F766E',
      strokeOpacity: 0.62,
      strokeWeight: 2,
      zIndex: 5,
    });
    return () => circle.setMap(null);
  }, [map, maps, userLocation.lat, userLocation.lon, viewRadiusKm]);

  const myLocationAccuracy = useMemo(() => {
    const accuracy = myLocation?.accuracy;
    if (typeof accuracy !== 'number' || !Number.isFinite(accuracy) || accuracy <= 0) return null;
    return Math.min(Math.max(accuracy, 25), 2000);
  }, [myLocation?.accuracy]);

  useEffect(() => {
    if (!map || !myLocation || myLocationAccuracy == null) return undefined;
    const circle = new maps.Circle({
      map,
      center: new maps.LatLng(myLocation.lat, myLocation.lon),
      radius: myLocationAccuracy,
      clickable: false,
      fillColor: '#2563EB',
      fillOpacity: 0.12,
      strokeColor: '#2563EB',
      strokeOpacity: 0.38,
      strokeWeight: 1,
      zIndex: 20,
    });
    return () => circle.setMap(null);
  }, [map, maps, myLocation, myLocationAccuracy]);

  useEffect(() => {
    if (!map || !myLocation) return undefined;
    const size = 34;
    const marker = new maps.Marker({
      map,
      position: new maps.LatLng(myLocation.lat, myLocation.lon),
      image: new maps.MarkerImage(
        myLocationMarkerUrl(),
        new maps.Size(size, size),
        { offset: new maps.Point(size / 2, size / 2) },
      ),
      title: myLocation.label || '내 위치',
      clickable: false,
      zIndex: 2000,
    });
    return () => marker.setMap(null);
  }, [map, maps, myLocation]);

  return (
    <section className="map-card google-map-card kakao-map-card" aria-label="카카오 지도 기반 강좌 검색">
      <div ref={initializeMap} style={{ width: '100%', height: '100%' }} />

      {map && branchesWithCoords.map((branch) => (
        <CenterMapMarker
          key={branch.id}
          map={map}
          maps={maps}
          branch={branch}
          courseCount={getBranchCourseCount(branch)}
          selected={selectedBranch?.id === branch.id || selectedBranchSet.has(branch.id)}
          highlighted={hoveredBranchId === branch.id}
          favorite={favoriteBranchSet.has(branch.id)}
          urgent={(branchUrgentCounts?.[branch.id] ?? 0) > 0}
          onClick={() => onBranchSelect(branch)}
          onHover={onBranchHover}
        />
      ))}

      <div className="map-visible-range-frame" aria-hidden="true" />
      <div className="map-visible-range-label" aria-live="polite">
        선택 지름 {viewDiameterKm}km
      </div>
      <MapMarkerLegend mapMode={mapMode} />
      {!map && (
        <div className="google-map-fallback">
          <strong>카카오 지도를 준비하는 중입니다.</strong>
        </div>
      )}
      {locationError && <div className="map-inline-error">{locationError}</div>}
    </section>
  );
}
