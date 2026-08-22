import { useEffect, useMemo, useState } from 'react';
import {
  CalendarOff,
  Check,
  CircleAlert,
  CircleDollarSign,
  Clock3,
  ExternalLink,
  LocateFixed,
  MapPin,
  Navigation,
  Phone,
  RefreshCw,
  RotateCcw,
  Search,
  X,
} from 'lucide-react';
import type { Branch } from '../api';
import { useDialogAccessibility } from '../hooks/useDialogAccessibility';
import { branchDisplayName } from '../utils/branchDisplay';
import {
  distanceKm,
  formatDistanceLabel,
  MAP_DIAMETER_OPTIONS_KM,
  type MapDiameterKm,
  type UserLocation,
} from '../utils/location';
import { type MapMode } from '../utils/mapScope';
import { mapBranchCourseCount } from '../utils/mapBranches';
import { kakaoDirectionsLink } from '../utils/kakaoMaps';
import { safeExternalUrl } from '../utils/safeUrl';
import MapSection from './MapSection';
import ProviderIcon from './ProviderIcon';

type LocationPickerModalProps = {
  open: boolean;
  branches: Branch[];
  providerFilters: string[];
  categoryFilters: string[];
  categoryFilterActive?: boolean;
  mapMode: MapMode;
  branchCourseCounts: Record<string, number>;
  branchOpenCounts: Record<string, number>;
  branchUrgentCounts: Record<string, number>;
  favoriteBranchIds: string[];
  selectedBranchIds: string[];
  userLocation: UserLocation;
  myLocation: UserLocation | null;
  locationLabel: string;
  locationError: string | null;
  diameterKm: MapDiameterKm;
  locating: boolean;
  usingCurrentLocation: boolean;
  debugMode: boolean;
  onClose: () => void;
  onBranchToggle: (branch: Branch) => void;
  onClearBranches: () => void;
  onDiameterChange: (diameter: MapDiameterKm) => void;
  onRequestCurrentLocation: () => void;
  onResetLocation: () => void;
  onMapCenterChange: (lat: number, lon: number) => void;
  onVisibleBranchIdsChange: (branchIds: string[]) => void;
};

export default function LocationPickerModal({
  open,
  branches,
  providerFilters,
  categoryFilters,
  categoryFilterActive = true,
  mapMode,
  branchCourseCounts,
  branchOpenCounts,
  branchUrgentCounts,
  favoriteBranchIds,
  selectedBranchIds,
  userLocation,
  myLocation,
  locationLabel,
  locationError,
  diameterKm,
  locating,
  usingCurrentLocation,
  debugMode,
  onClose,
  onBranchToggle,
  onClearBranches,
  onDiameterChange,
  onRequestCurrentLocation,
  onResetLocation,
  onMapCenterChange,
  onVisibleBranchIdsChange,
}: LocationPickerModalProps) {
  const [query, setQuery] = useState('');
  const [hoveredBranchId, setHoveredBranchId] = useState<string | null>(null);
  const [focusedBranchId, setFocusedBranchId] = useState<string | null>(null);
  const dialogRef = useDialogAccessibility<HTMLElement>(open, onClose);
  const selectedBranchSet = useMemo(() => new Set(selectedBranchIds), [selectedBranchIds]);

  useEffect(() => {
    if (!open) return;
    setQuery('');
    setHoveredBranchId(null);
    setFocusedBranchId(null);
  }, [open]);

  const sortedBranches = useMemo(
    () =>
      [...branches].sort(
        (left, right) =>
          distanceKm(userLocation, left) - distanceKm(userLocation, right)
          || branchDisplayName(left).localeCompare(branchDisplayName(right), 'ko-KR'),
      ),
    [branches, userLocation],
  );

  const visibleBranches = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase('ko-KR');
    if (!normalizedQuery) return sortedBranches;
    return sortedBranches.filter((branch) => {
      const searchable = [
        branchDisplayName(branch),
        branch.provider_label,
        branch.provider,
        branch.address,
      ].filter(Boolean).join(' ').toLocaleLowerCase('ko-KR');
      return searchable.includes(normalizedQuery);
    });
  }, [query, sortedBranches]);

  const selectedBranches = useMemo(
    () => sortedBranches.filter((branch) => selectedBranchSet.has(branch.id)),
    [selectedBranchSet, sortedBranches],
  );
  const focusedBranch = useMemo(
    () => sortedBranches.find((branch) => branch.id === focusedBranchId) || null,
    [focusedBranchId, sortedBranches],
  );
  const focusedBranchCourseCount = focusedBranch
    ? mapBranchCourseCount(focusedBranch, mapMode, branchCourseCounts)
    : 0;
  const focusedBranchOpenCount = focusedBranch ? branchOpenCounts[focusedBranch.id] ?? 0 : 0;
  const focusedBranchUrgentCount = focusedBranch ? branchUrgentCounts[focusedBranch.id] ?? 0 : 0;
  const focusedBranchDistance = focusedBranch
    ? formatDistanceLabel(distanceKm(userLocation, focusedBranch))
    : '';
  const focusedBranchWebsite = safeExternalUrl(focusedBranch?.website_url);
  const focusedBranchPhone = focusedBranch?.phone?.trim() || '';
  const focusedBranchPhoneHref = focusedBranchPhone.replace(/[^\d+]/g, '');
  const focusedBranchDirections = focusedBranch
    ? kakaoDirectionsLink({
      name: branchDisplayName(focusedBranch),
      address: focusedBranch.address,
      lat: focusedBranch.lat,
      lon: focusedBranch.lon,
    })
    : '';

  const handleBranchToggle = (branch: Branch) => {
    setFocusedBranchId(branch.id);
    onBranchToggle(branch);
  };

  if (!open) return null;

  return (
    <div className="location-picker-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        ref={dialogRef}
        className="location-picker-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="location-picker-title"
        tabIndex={-1}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="location-picker-header">
          <div>
            <span className="location-picker-title-icon" aria-hidden="true">
              <MapPin size={20} strokeWidth={2} />
            </span>
            <div>
              <h2 id="location-picker-title">위치·지점 선택</h2>
              <p>{locationLabel} · 지름 {diameterKm}km · {branches.length.toLocaleString('ko-KR')}개 지점</p>
            </div>
          </div>
          <button className="location-picker-close" type="button" title="닫기" aria-label="닫기" onClick={onClose}>
            <X size={20} strokeWidth={2} aria-hidden="true" />
          </button>
        </header>

        <div className="location-picker-toolbar">
          <div className="location-picker-location-actions">
            <button type="button" disabled={locating} onClick={onRequestCurrentLocation}>
              <LocateFixed size={17} strokeWidth={2} aria-hidden="true" />
              {locating ? '확인 중' : usingCurrentLocation ? '현재 위치 사용 중' : '현재 위치'}
            </button>
            <button type="button" onClick={onResetLocation}>
              <RotateCcw size={16} strokeWidth={2} aria-hidden="true" />
              기본 위치
            </button>
          </div>
          <div className="location-picker-radius" role="radiogroup" aria-label="검색 지름">
            {MAP_DIAMETER_OPTIONS_KM.map((diameter) => (
              <button
                key={diameter}
                className={diameterKm === diameter ? 'active' : undefined}
                type="button"
                role="radio"
                aria-checked={diameterKm === diameter}
                onClick={() => onDiameterChange(diameter)}
              >
                {diameter}km
              </button>
            ))}
          </div>
          {locationError && (
            <div className="location-picker-location-error" role="alert">
              <CircleAlert size={17} strokeWidth={2} aria-hidden="true" />
              <span>{locationError}</span>
              <button type="button" disabled={locating} onClick={onRequestCurrentLocation}>
                <RefreshCw size={14} strokeWidth={2} aria-hidden="true" />
                다시 시도
              </button>
            </div>
          )}
        </div>

        <div className="location-picker-content">
          <aside className="location-picker-branch-panel" aria-label="지점 목록">
            <div className="location-picker-search">
              <Search size={17} strokeWidth={2} aria-hidden="true" />
              <input
                type="search"
                value={query}
                placeholder="지점명 또는 주소 검색"
                aria-label="지점 검색"
                onChange={(event) => setQuery(event.currentTarget.value)}
              />
              {query && (
                <button type="button" title="검색어 지우기" aria-label="검색어 지우기" onClick={() => setQuery('')}>
                  <X size={15} strokeWidth={2} aria-hidden="true" />
                </button>
              )}
            </div>

            <div className="location-picker-list-heading">
              <strong>지점 목록</strong>
              <span>{visibleBranches.length.toLocaleString('ko-KR')}개</span>
            </div>

            <div className="location-picker-branch-list">
              {visibleBranches.map((branch) => {
                const selected = selectedBranchSet.has(branch.id);
                const distanceLabel = formatDistanceLabel(distanceKm(userLocation, branch));
                return (
                  <button
                    key={branch.id}
                    className={selected ? 'selected' : undefined}
                    type="button"
                    aria-pressed={selected}
                    onMouseEnter={() => setHoveredBranchId(branch.id)}
                    onMouseLeave={() => setHoveredBranchId(null)}
                    onFocus={() => setHoveredBranchId(branch.id)}
                    onBlur={() => setHoveredBranchId(null)}
                    onClick={() => handleBranchToggle(branch)}
                  >
                    <ProviderIcon
                      providerName={branch.provider_label || branch.provider}
                      providerType={branch.provider}
                      centerName={branch.name}
                      websiteUrl={branch.website_url}
                      faviconUrl={branch.favicon_url}
                      size="small"
                      active={selected}
                    />
                    <span>
                      <strong>{branchDisplayName(branch)}</strong>
                      <small>{branch.address || branch.provider_label || branch.provider}</small>
                    </span>
                    <em>{distanceLabel}</em>
                    <i aria-hidden="true">{selected && <Check size={15} strokeWidth={2.5} />}</i>
                  </button>
                );
              })}
              {visibleBranches.length === 0 && (
                <div className="location-picker-empty">
                  <MapPin size={22} strokeWidth={1.8} aria-hidden="true" />
                  <span>표시할 지점이 없습니다.</span>
                </div>
              )}
            </div>
          </aside>

          <div className="location-picker-map">
            <MapSection
              branches={branches}
              providerFilters={providerFilters}
              categoryFilters={categoryFilters}
              categoryFilterActive={categoryFilterActive}
              mapMode={mapMode}
              branchCourseCounts={branchCourseCounts}
              branchOpenCounts={branchOpenCounts}
              branchUrgentCounts={branchUrgentCounts}
              favoriteBranchIds={favoriteBranchIds}
              userLocation={userLocation}
              myLocation={myLocation}
              locationError={null}
              selectedBranch={focusedBranch}
              selectedBranchIds={selectedBranchIds}
              hoveredBranchId={hoveredBranchId}
              viewDiameterKm={diameterKm}
              debugMode={debugMode}
              onBranchSelect={(branch) => {
                if (branch) {
                  handleBranchToggle(branch);
                } else {
                  setFocusedBranchId(null);
                }
              }}
              onBranchHover={setHoveredBranchId}
              onMapCenterChange={onMapCenterChange}
              onVisibleBranchIdsChange={onVisibleBranchIdsChange}
            />
            {focusedBranch && (
              <aside className="location-picker-branch-detail" aria-label="선택 지점 정보" aria-live="polite">
                <header>
                  <ProviderIcon
                    providerName={focusedBranch.provider_label || focusedBranch.provider}
                    providerType={focusedBranch.provider}
                    centerName={focusedBranch.name}
                    websiteUrl={focusedBranch.website_url}
                    faviconUrl={focusedBranch.favicon_url}
                    size="small"
                    active
                  />
                  <div>
                    <strong>{branchDisplayName(focusedBranch)}</strong>
                    <small>
                      {focusedBranch.provider_label || focusedBranch.provider}
                      {focusedBranchDistance && ` · ${focusedBranchDistance}`}
                    </small>
                  </div>
                  <button
                    type="button"
                    title="지점 정보 닫기"
                    aria-label="지점 정보 닫기"
                    onClick={() => setFocusedBranchId(null)}
                  >
                    <X size={17} strokeWidth={2} aria-hidden="true" />
                  </button>
                </header>

                <div className="location-picker-branch-detail-status">
                  <span className={selectedBranchSet.has(focusedBranch.id) ? 'selected' : undefined}>
                    {selectedBranchSet.has(focusedBranch.id) ? (
                      <>
                        <Check size={13} strokeWidth={2.5} aria-hidden="true" />
                        필터 적용
                      </>
                    ) : '필터 미적용'}
                  </span>
                  <span>전체 {focusedBranchCourseCount.toLocaleString('ko-KR')}개</span>
                  <span>접수중 {focusedBranchOpenCount.toLocaleString('ko-KR')}개</span>
                  {focusedBranchUrgentCount > 0 && (
                    <span>마감임박 {focusedBranchUrgentCount.toLocaleString('ko-KR')}개</span>
                  )}
                </div>

                <dl className="location-picker-branch-detail-list">
                  {focusedBranch.address && (
                    <div className="wide">
                      <dt><MapPin size={15} strokeWidth={1.9} aria-hidden="true" />주소</dt>
                      <dd>{focusedBranch.address}</dd>
                    </div>
                  )}
                  {focusedBranchPhone && (
                    <div>
                      <dt><Phone size={15} strokeWidth={1.9} aria-hidden="true" />전화</dt>
                      <dd>{focusedBranchPhone}</dd>
                    </div>
                  )}
                  {focusedBranch.operating_hours && (
                    <div>
                      <dt><Clock3 size={15} strokeWidth={1.9} aria-hidden="true" />운영시간</dt>
                      <dd>{focusedBranch.operating_hours}</dd>
                    </div>
                  )}
                  {focusedBranch.regular_holiday && (
                    <div>
                      <dt><CalendarOff size={15} strokeWidth={1.9} aria-hidden="true" />휴관일</dt>
                      <dd>{focusedBranch.regular_holiday}</dd>
                    </div>
                  )}
                  {focusedBranch.admission_fee && (
                    <div>
                      <dt><CircleDollarSign size={15} strokeWidth={1.9} aria-hidden="true" />이용요금</dt>
                      <dd>{focusedBranch.admission_fee}</dd>
                    </div>
                  )}
                </dl>

                <div className="location-picker-branch-detail-actions">
                  <a href={focusedBranchDirections} target="_blank" rel="noopener noreferrer">
                    <Navigation size={15} strokeWidth={2} aria-hidden="true" />
                    {focusedBranch.lat != null && focusedBranch.lon != null ? '길찾기' : '카카오맵'}
                  </a>
                  {focusedBranchWebsite && (
                    <a href={focusedBranchWebsite} target="_blank" rel="noopener noreferrer">
                      <ExternalLink size={15} strokeWidth={2} aria-hidden="true" />
                      홈페이지
                    </a>
                  )}
                  {focusedBranchPhoneHref && (
                    <a href={`tel:${focusedBranchPhoneHref}`}>
                      <Phone size={15} strokeWidth={2} aria-hidden="true" />
                      전화
                    </a>
                  )}
                </div>
              </aside>
            )}
          </div>
        </div>

        <footer className="location-picker-footer">
          <div className="location-picker-selection">
            <strong>선택 지점 {selectedBranchIds.length.toLocaleString('ko-KR')}개</strong>
            <div>
              {selectedBranches.slice(0, 4).map((branch) => (
                <button key={branch.id} type="button" title="필터에서 제거" onClick={() => onBranchToggle(branch)}>
                  <span>{branchDisplayName(branch)}</span>
                  <X size={13} strokeWidth={2.2} aria-hidden="true" />
                </button>
              ))}
              {selectedBranches.length > 4 && <span>+{selectedBranches.length - 4}</span>}
            </div>
          </div>
          <div className="location-picker-footer-actions">
            {selectedBranchIds.length > 0 && (
              <button className="location-picker-clear" type="button" onClick={onClearBranches}>선택 해제</button>
            )}
            <button className="location-picker-apply" type="button" onClick={onClose}>
              적용
            </button>
          </div>
        </footer>
      </section>
    </div>
  );
}
