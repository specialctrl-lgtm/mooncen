import { useCallback, useState } from 'react';
import {
  Clock3,
  ExternalLink,
  MapPin,
  Navigation,
  Phone,
  X,
} from 'lucide-react';
import type { ClassItem } from '../data/mockData';
import { useDialogAccessibility } from '../hooks/useDialogAccessibility';
import { kakaoDirectionsLink } from '../utils/kakaoMaps';
import { safeExternalUrl } from '../utils/safeUrl';
import KakaoLocationMap from './KakaoLocationMap';

type CourseLocationModalProps = {
  item: ClassItem | null;
  onClose: () => void;
};

function usefulText(value?: string | null) {
  const normalized = value?.trim();
  return normalized || null;
}

export default function CourseLocationModal({ item, onClose }: CourseLocationModalProps) {
  const open = Boolean(item);
  const dialogRef = useDialogAccessibility<HTMLElement>(open, onClose);
  const [resolvedCoordinates, setResolvedCoordinates] = useState<{
    key: string;
    lat: number;
    lon: number;
  } | null>(null);
  const locationKey = item
    ? `${item.id}:${item.venueAddress || item.branch?.address || ''}`
    : '';
  const handleCoordinatesResolved = useCallback((lat: number, lon: number) => {
    setResolvedCoordinates({ key: locationKey, lat, lon });
  }, [locationKey]);

  if (!item) return null;

  const branch = item.branch;
  const locationName = usefulText(item.venueName)
    || usefulText(item.center)
    || usefulText(branch?.name)
    || '강좌 장소';
  const address = usefulText(item.venueAddress) || usefulText(branch?.address);
  const phone = usefulText(branch?.phone);
  const phoneHref = phone?.replace(/[^\d+]/g, '') || null;
  const website = safeExternalUrl(branch?.website_url);
  const hasBranchCoordinates = typeof branch?.lat === 'number'
    && Number.isFinite(branch.lat)
    && typeof branch.lon === 'number'
    && Number.isFinite(branch.lon);
  const geocodedCoordinates = resolvedCoordinates?.key === locationKey ? resolvedCoordinates : null;
  const directionsLat = hasBranchCoordinates ? branch.lat : geocodedCoordinates?.lat;
  const directionsLon = hasBranchCoordinates ? branch.lon : geocodedCoordinates?.lon;
  const hasDirectionsCoordinates = typeof directionsLat === 'number'
    && Number.isFinite(directionsLat)
    && typeof directionsLon === 'number'
    && Number.isFinite(directionsLon);
  const directionsUrl = kakaoDirectionsLink({
    name: locationName,
    address,
    lat: directionsLat,
    lon: directionsLon,
  });
  const distanceLabel = typeof item.distanceKm === 'number' && Number.isFinite(item.distanceKm)
    ? `${item.distanceKm < 10 ? item.distanceKm.toFixed(1) : Math.round(item.distanceKm)}km`
    : null;

  return (
    <div className="course-location-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        ref={dialogRef}
        className="course-location-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="course-location-title"
        tabIndex={-1}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="course-location-header">
          <span aria-hidden="true"><MapPin size={20} strokeWidth={2} /></span>
          <div>
            <h2 id="course-location-title">{locationName}</h2>
            <p>{item.title}</p>
          </div>
          <button type="button" title="닫기" aria-label="위치 팝업 닫기" onClick={onClose}>
            <X size={20} strokeWidth={2} aria-hidden="true" />
          </button>
        </header>

        <div className="course-location-map">
          <KakaoLocationMap
            name={locationName}
            address={address}
            lat={branch?.lat}
            lon={branch?.lon}
            onCoordinatesResolved={handleCoordinatesResolved}
          />
        </div>

        <div className="course-location-details">
          <dl>
            <div>
              <dt><MapPin size={16} strokeWidth={1.9} aria-hidden="true" />주소</dt>
              <dd>{address || '상세 주소가 등록되지 않았습니다.'}</dd>
            </div>
            {distanceLabel && (
              <div>
                <dt><Navigation size={16} strokeWidth={1.9} aria-hidden="true" />거리</dt>
                <dd>기준 위치에서 {distanceLabel}</dd>
              </div>
            )}
            {phone && (
              <div>
                <dt><Phone size={16} strokeWidth={1.9} aria-hidden="true" />전화</dt>
                <dd>{phone}</dd>
              </div>
            )}
            {usefulText(branch?.operating_hours) && (
              <div>
                <dt><Clock3 size={16} strokeWidth={1.9} aria-hidden="true" />운영시간</dt>
                <dd>{branch?.operating_hours}</dd>
              </div>
            )}
          </dl>

          <div className="course-location-actions">
            <a href={directionsUrl} target="_blank" rel="noopener noreferrer">
              <Navigation size={16} strokeWidth={2} aria-hidden="true" />
              {hasDirectionsCoordinates ? '길찾기' : '카카오맵'}
            </a>
            {phoneHref && (
              <a href={`tel:${phoneHref}`}>
                <Phone size={16} strokeWidth={2} aria-hidden="true" />
                전화
              </a>
            )}
            {website && (
              <a href={website} target="_blank" rel="noopener noreferrer">
                <ExternalLink size={16} strokeWidth={2} aria-hidden="true" />
                홈페이지
              </a>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
