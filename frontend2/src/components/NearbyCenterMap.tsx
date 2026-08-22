import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react';
import { CircleAlert, RefreshCw } from 'lucide-react';
import type { Branch } from '../api';
import { branchDisplayName } from '../utils/branchDisplay';
import {
  diameterToRadiusKm,
  distanceKm,
  formatDistanceLabel,
  MAP_DIAMETER_OPTIONS_KM,
  type MapDiameterKm,
  type UserLocation,
} from '../utils/location';
import ProviderIcon from './ProviderIcon';
import { kakaoDirectionsLink } from '../utils/kakaoMaps';
import { safeExternalUrl } from '../utils/safeUrl';
import { isFacilityInfoBranch } from '../utils/mapScope';
import { mapBranchCourseCount } from '../utils/mapBranches';

type NearbyCenterMapProps = {
  branches: Branch[];
  branchCourseCounts?: Record<string, number>;
  branchOpenCounts?: Record<string, number>;
  branchUrgentCounts?: Record<string, number>;
  userLocation: UserLocation;
  selectedBranch: Branch | null;
  selectedBranchIds?: string[];
  hoveredBranchId?: string | null;
  diameterKm: MapDiameterKm;
  mapMode?: 'provider' | 'education' | 'experience';
  onDiameterChange: (diameterKm: MapDiameterKm) => void;
  onBranchSelect: (branch: Branch) => void;
  onBranchHover?: (branchId: string | null) => void;
  locating?: boolean;
  usingCurrentLocation?: boolean;
  onRequestCurrentLocation?: () => void;
  onStopUsingCurrentLocation?: () => void;
  locationError?: string | null;
  title?: string;
  children: ReactNode;
};

function openCourseCount(branch: Branch) {
  return branch.open_course_count ?? 0;
}

function diameterTone(diameter: number) {
  if (diameter <= 5) return { key: 'near', color: '#14B8A6', glow: '#CCFBF1' };
  if (diameter <= 10) return { key: 'middle', color: '#F59E0B', glow: '#FEF3C7' };
  return { key: 'wide', color: '#1D9BF0', glow: '#DBEAFE' };
}

function DiameterRangeIcon({ diameter, active }: { diameter: number; active: boolean }) {
  const tone = diameterTone(diameter);
  return (
    <span
      className={`radius-range-icon radius-range-icon-${tone.key} ${active ? 'active' : ''}`}
      style={{
        '--radius-color': tone.color,
        '--radius-glow': tone.glow,
      } as CSSProperties}
      aria-hidden="true"
    >
      <svg viewBox="0 0 72 76" focusable="false">
        <ellipse className="radius-shadow" cx="36" cy="68" rx="18" ry="5" />
        <path className="radius-pin-tail" d="M36 72 25 56h22L36 72Z" />
        <circle className="radius-pin-disc" cx="36" cy="34" r="28" />
        <ellipse className="radius-orbit radius-orbit-outer" cx="36" cy="36" rx="21" ry="14" />
        <ellipse className="radius-orbit radius-orbit-inner" cx="36" cy="43" rx="13" ry="5" />
        <path className="radius-grid" d="M18 43h36M36 30v25" />
        <path className="radius-location-pin" d="M36 17c-7 0-12 5.5-12 12.2 0 9.1 12 22.8 12 22.8s12-13.7 12-22.8C48 22.5 43 17 36 17Zm0 17a5 5 0 1 1 0-10 5 5 0 0 1 0 10Z" />
      </svg>
    </span>
  );
}

export default function NearbyCenterMap({
  branches,
  branchCourseCounts,
  branchOpenCounts,
  branchUrgentCounts,
  userLocation,
  selectedBranch,
  selectedBranchIds = [],
  hoveredBranchId = null,
  diameterKm,
  mapMode = 'provider',
  onDiameterChange,
  onBranchSelect,
  onBranchHover,
  locating = false,
  usingCurrentLocation = false,
  onRequestCurrentLocation,
  onStopUsingCurrentLocation,
  locationError = null,
  title = '내 주변 문화센터',
  children,
}: NearbyCenterMapProps) {
  const [mobileMapOpen, setMobileMapOpen] = useState(false);
  const [visibleBranchLimit, setVisibleBranchLimit] = useState(5);
  const isEducationMode = mapMode !== 'provider';
  const radiusKm = diameterToRadiusKm(diameterKm);

  useEffect(() => {
    setVisibleBranchLimit(5);
  }, [diameterKm, mapMode, userLocation.lat, userLocation.lon]);

  const nearbyBranches = useMemo(
    () =>
      branches
        .map((branch) => ({ branch, distance: distanceKm(userLocation, branch) }))
        .filter(({ branch, distance }) => (
          distance <= radiusKm &&
          (mapBranchCourseCount(branch, mapMode, branchCourseCounts) > 0 || isFacilityInfoBranch(branch))
        ))
        .sort((left, right) => (left.distance ?? 0) - (right.distance ?? 0))
        .map(({ branch }) => branch),
    [branchCourseCounts, branches, mapMode, radiusKm, userLocation],
  );
  const selectedBranchSet = useMemo(() => new Set(selectedBranchIds), [selectedBranchIds]);
  const branchCountLabel = nearbyBranches.length.toLocaleString('ko-KR');
  const selectedCountLabel = selectedBranchSet.size ? ` · 선택 ${selectedBranchSet.size.toLocaleString('ko-KR')}개` : '';
  const visibleNearbyBranches = nearbyBranches.slice(0, visibleBranchLimit);
  const hiddenBranchCount = Math.max(nearbyBranches.length - visibleNearbyBranches.length, 0);
  const branchListExpanded = visibleBranchLimit > 5 && nearbyBranches.length > 5;
  const singleFilteredBranch =
    selectedBranchSet.size === 1
      ? nearbyBranches.find((branch) => selectedBranchSet.has(branch.id)) || null
      : null;
  const summaryBranch = selectedBranch || singleFilteredBranch;
  const summaryIsFilteredBranch = Boolean(summaryBranch && selectedBranchSet.has(summaryBranch.id));
  const summaryCourseCount = summaryBranch
    ? mapBranchCourseCount(summaryBranch, mapMode, branchCourseCounts)
    : 0;
  const summaryOpenCount = summaryBranch ? branchOpenCounts?.[summaryBranch.id] ?? openCourseCount(summaryBranch) : 0;
  const summaryUrgentCount = summaryBranch ? branchUrgentCounts?.[summaryBranch.id] ?? 0 : 0;
  const summaryClosedCount = Math.max(summaryCourseCount - summaryOpenCount - summaryUrgentCount, 0);
  const summaryDistanceLabel = summaryBranch ? formatDistanceLabel(distanceKm(userLocation, summaryBranch)) : '';
  const summaryIsFacilityInfo = Boolean(summaryBranch && isFacilityInfoBranch(summaryBranch) && summaryCourseCount <= 0);
  const summaryFacilityStats = summaryBranch ? [
    ['유형', summaryBranch.facility_type || summaryBranch.facility_category || '시설'],
    ['운영', summaryBranch.operating_hours || '정보 없음'],
    ['휴관', summaryBranch.regular_holiday || '정보 없음'],
    ['요금', summaryBranch.admission_fee || '정보 없음'],
  ] : [];
  const summaryBasicInfoRows = summaryBranch ? (
    summaryIsFacilityInfo ? [
      ['주소', summaryBranch.address],
    ] : [
      ['주소', summaryBranch.address],
      ['운영', summaryBranch.operating_hours],
      ['휴관', summaryBranch.regular_holiday],
      ['요금', summaryBranch.admission_fee],
      ['유형', summaryBranch.facility_type || summaryBranch.facility_category],
    ]
  ).filter((row): row is [string, string] => Boolean(row[1])) : [];
  const summaryDirectionsUrl = summaryBranch
    ? kakaoDirectionsLink({
      name: branchDisplayName(summaryBranch),
      address: summaryBranch.address,
      lat: summaryBranch.lat,
      lon: summaryBranch.lon,
    })
    : '';
  const summaryWebsiteUrl = safeExternalUrl(summaryBranch?.website_url);

  return (
    <section className="nearby-map-section" aria-labelledby="nearby-map-heading">
      {locationError && (
        <div className="nearby-location-error" role="alert">
          <CircleAlert size={18} strokeWidth={2} aria-hidden="true" />
          <span>{locationError}</span>
          {onRequestCurrentLocation && (
            <button type="button" disabled={locating} onClick={onRequestCurrentLocation}>
              <RefreshCw size={14} strokeWidth={2} aria-hidden="true" />
              다시 시도
            </button>
          )}
        </div>
      )}
      <div className={`nearby-map-layout ${mobileMapOpen ? 'map-open' : ''}`}>
        <aside
          className={[
            'nearby-center-list',
            isEducationMode ? `${mapMode}-mode education-mode` : 'provider-mode',
            branchListExpanded ? 'branch-list-expanded' : '',
          ].filter(Boolean).join(' ')}
          aria-label="지점 목록"
        >
          <div className="nearby-list-heading">
            <h2 id="nearby-map-heading">지점 목록</h2>
            <p>{title} · 지름 {diameterKm}km 내 {branchCountLabel}개 지점{selectedCountLabel}</p>
          </div>
          <div className="nearby-center-list-body">
            {nearbyBranches.length ? visibleNearbyBranches.map((branch) => {
              const checked = selectedBranchSet.has(branch.id);
              const listCount = mapBranchCourseCount(branch, mapMode, branchCourseCounts);
              const distanceLabel = formatDistanceLabel(distanceKm(userLocation, branch));
              const displayName = branchDisplayName(branch);

              return (
                <button
                  key={branch.id}
                  className={[
                    selectedBranch?.id === branch.id ? 'map-active' : '',
                    hoveredBranchId === branch.id ? 'map-hover' : '',
                    checked ? 'active multi-selected' : '',
                    isEducationMode ? `${mapMode}-mode education-mode` : '',
                  ].filter(Boolean).join(' ') || undefined}
                  type="button"
                  aria-pressed={checked}
                  aria-current={selectedBranch?.id === branch.id ? 'true' : undefined}
                  onMouseEnter={() => onBranchHover?.(branch.id)}
                  onMouseLeave={() => onBranchHover?.(null)}
                  onFocus={() => onBranchHover?.(branch.id)}
                  onBlur={() => onBranchHover?.(null)}
                  onClick={() => onBranchSelect(branch)}
                >
                  <ProviderIcon
                    providerName={branch.provider_label || branch.provider}
                    providerType={branch.provider}
                    centerName={branch.name}
                    websiteUrl={branch.website_url}
                    faviconUrl={branch.favicon_url}
                    size="small"
                    active={checked || selectedBranch?.id === branch.id}
                  />
                  <span className="nearby-center-meta">
                    <strong>{displayName}</strong>
                    {distanceLabel && <small>{distanceLabel}</small>}
                  </span>
                  <em>
                    <b>{isFacilityInfoBranch(branch) && listCount <= 0 ? '시설' : `${listCount.toLocaleString('ko-KR')}개`}</b>
                  </em>
                </button>
              );
            }) : (
              <p>선택한 반경에 표시할 지점이 없습니다.</p>
            )}
          </div>
          {hiddenBranchCount > 0 && (
            <button
              className="nearby-branch-more-button"
              type="button"
              onClick={() => setVisibleBranchLimit((limit) => Math.min(limit + 10, nearbyBranches.length))}
            >
              + {hiddenBranchCount.toLocaleString('ko-KR')}개 지점 더보기
            </button>
          )}
        </aside>
        <div className="nearby-map-canvas">
          <div className="nearby-map-toolbar">
            <h2>지도</h2>
            <div className="nearby-map-actions">
              {onRequestCurrentLocation && (
                <button
                  className="nearby-current-location-button"
                  type="button"
                  disabled={locating}
                  onClick={usingCurrentLocation && onStopUsingCurrentLocation ? onStopUsingCurrentLocation : onRequestCurrentLocation}
                >
                  {locating ? '위치 확인 중…' : usingCurrentLocation ? '내 위치 사용 중 · 지우기' : '내 위치 사용'}
                </button>
              )}
              <div className="nearby-radius-control" role="radiogroup" aria-label="검색 지름 선택">
                {MAP_DIAMETER_OPTIONS_KM.map((diameter) => {
                  const tone = diameterTone(diameter);
                  return (
                    <button
                      key={diameter}
                      className={diameterKm === diameter ? 'active' : undefined}
                      type="button"
                      role="radio"
                      aria-checked={diameterKm === diameter}
                      aria-label={`지름 ${diameter}km`}
                      style={{
                        '--radius-color': tone.color,
                        '--radius-glow': tone.glow,
                      } as CSSProperties}
                      onClick={() => onDiameterChange(diameter)}
                    >
                      <DiameterRangeIcon diameter={diameter} active={diameterKm === diameter} />
                      <span>{diameter}km</span>
                    </button>
                  );
                })}
              </div>
              <button
                className="nearby-mobile-map-toggle"
                type="button"
                aria-expanded={mobileMapOpen}
                onClick={() => setMobileMapOpen((open) => !open)}
              >
                {mobileMapOpen ? '지도 닫기' : '지도 보기'}
              </button>
            </div>
          </div>
          {children}
        </div>
        <aside className="selected-branch-summary-card" aria-label="선택 지점 요약">
          <span className="summary-card-kicker">{summaryIsFilteredBranch ? '필터 지점' : '선택 지점'}</span>
          {summaryBranch ? (
            <>
              <div className="summary-branch-title">
                <ProviderIcon
                  providerName={summaryBranch.provider_label || summaryBranch.provider}
                  providerType={summaryBranch.provider}
                  centerName={summaryBranch.name}
                  websiteUrl={summaryBranch.website_url}
                  faviconUrl={summaryBranch.favicon_url}
                  size="small"
                  active
                />
                <div>
                  <strong>{branchDisplayName(summaryBranch)}</strong>
                  <small>
                    {summaryDistanceLabel || '거리 정보 없음'}
                    {summaryIsFilteredBranch && <b>필터 적용</b>}
                  </small>
                </div>
              </div>
              <dl className={`summary-branch-stats ${summaryIsFacilityInfo ? 'summary-branch-facility-stats' : ''}`}>
                {summaryIsFacilityInfo ? summaryFacilityStats.map(([label, value]) => (
                  <div key={label}>
                    <dt>{label}</dt>
                    <dd>{value}</dd>
                  </div>
                )) : (
                  <>
                    <div>
                      <dt>거리</dt>
                      <dd>{summaryDistanceLabel || '정보 없음'}</dd>
                    </div>
                    <div>
                      <dt>현재 조건</dt>
                      <dd>{summaryCourseCount.toLocaleString('ko-KR')}개</dd>
                    </div>
                    <div>
                      <dt>접수중</dt>
                      <dd>{summaryOpenCount.toLocaleString('ko-KR')}개</dd>
                    </div>
                    <div>
                      <dt>마감임박</dt>
                      <dd>{summaryUrgentCount.toLocaleString('ko-KR')}개</dd>
                    </div>
                    <div>
                      <dt>마감</dt>
                      <dd>{summaryClosedCount.toLocaleString('ko-KR')}개</dd>
                    </div>
                  </>
                )}
              </dl>
              {summaryBasicInfoRows.length > 0 && (
                <dl className="summary-branch-basic-info">
                  {summaryBasicInfoRows.map(([label, value]) => (
                    <div key={label}>
                      <dt>{label}</dt>
                      <dd>{value}</dd>
                    </div>
                  ))}
                </dl>
              )}
              <div className="summary-branch-actions">
                {summaryDirectionsUrl && (
                  <a href={summaryDirectionsUrl} target="_blank" rel="noreferrer">
                    {summaryBranch.lat != null && summaryBranch.lon != null ? '길찾기' : '카카오맵'}
                  </a>
                )}
                {summaryWebsiteUrl && (
                  <a href={summaryWebsiteUrl} target="_blank" rel="noopener noreferrer">홈페이지</a>
                )}
                {summaryBranch.phone && <a href={`tel:${summaryBranch.phone}`}>전화하기</a>}
                <button className="summary-branch-wide-action" type="button" onClick={() => onBranchSelect(summaryBranch)}>
                  {summaryIsFilteredBranch ? '필터에서 제거' : '필터에 추가'}
                </button>
              </div>
            </>
          ) : (
            <div className="summary-branch-empty">
              <strong>지점을 선택해주세요</strong>
              <p>지점 목록이나 지도 마커를 선택하면 현재 조건의 강좌 수가 표시됩니다.</p>
            </div>
          )}
        </aside>
      </div>
    </section>
  );
}
