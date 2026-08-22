import {
  CalendarDays,
  Check,
  ClipboardCheck,
  ExternalLink,
  Heart,
  MapPin,
} from 'lucide-react';
import type { ClassItem } from '../data/mockData';
import {
  formatApplicationPeriod,
  formatCoursePrice,
  formatCourseSchedule,
  hasUsefulCourseText,
} from '../utils/courseCardDisplay';
import { firstSafeExternalUrl } from '../utils/safeUrl';

type ClassCardProps = {
  item: ClassItem;
  isFavorite: boolean;
  isCompared: boolean;
  onToggleFavorite: (item: ClassItem) => void;
  onApply: (item: ClassItem) => void;
  onToggleCompare: (item: ClassItem) => void;
  onOpenDetails: (item: ClassItem) => void;
  onOpenLocation: (item: ClassItem) => void;
};

function materialFeeLabel(item: ClassItem) {
  return item.materialFee > 0 ? `재료비 ${item.materialFee.toLocaleString('ko-KR')}원` : '';
}

function shortStatusLabel(item: Pick<ClassItem, 'status' | 'statusCode' | 'statusLabel'>) {
  if (item.statusCode === 'DEADLINE') return '마감임박';
  if (item.statusCode === 'SCHEDULED') return '접수예정';
  if (item.statusCode === 'CLOSED') return '접수마감';
  if (item.statusCode === 'WAITING') return '대기접수';
  if (item.statusCode === 'OPEN') return '접수중';

  const status = `${item.statusLabel || ''} ${item.status || ''}`;
  if (/마감임박|임박/i.test(status)) return '마감임박';
  if (/예정|신규/i.test(status)) return '접수예정';
  if (/마감/i.test(status)) return '접수마감';
  if (/대기/i.test(status)) return '대기접수';
  if (/접수|모집|OPEN/i.test(status)) return '접수중';
  return status.trim().slice(0, 8) || '상태 확인';
}

function statusClass(item: Pick<ClassItem, 'statusCode' | 'status' | 'statusLabel'>) {
  const status = `${item.statusCode || ''} ${item.status || ''} ${item.statusLabel || ''}`;
  if (/DEADLINE|마감임박|임박/i.test(status)) return 'deadline';
  if (/SCHEDULED|신규|예정/i.test(status)) return 'scheduled';
  if (/WAITING|대기/i.test(status)) return 'waiting';
  if (/CLOSED|마감/i.test(status)) return 'closed';
  return 'open';
}

function categoryTags(item: ClassItem) {
  return [...new Set([item.category, item.programType, ...item.tags])]
    .map((value) => value?.trim())
    .filter((value): value is string => Boolean(value) && hasUsefulCourseText(value) && value !== '강좌')
    .slice(0, 2);
}

function locationLabel(item: ClassItem) {
  const provider = hasUsefulCourseText(item.providerLabel) ? item.providerLabel.trim() : '';
  const center = hasUsefulCourseText(item.center) ? item.center.trim() : '';
  const compactProvider = provider.replace(/\s+/g, '');
  const compactCenter = center.replace(/\s+/g, '');
  const exactPublicBranch = center && (
    item.provider.trim().toUpperCase().startsWith('MUNI_')
    || item.serviceGroup?.replace(/\s+/g, '') === '공공강좌'
  );
  const place = exactPublicBranch
    ? center
    : center && provider && !compactCenter.includes(compactProvider) && !compactProvider.includes(compactCenter)
    ? `${provider} ${center}`
    : center || provider || '장소는 상세 페이지에서 확인';
  const distance = typeof item.distanceKm === 'number' && Number.isFinite(item.distanceKm)
    ? `${item.distanceKm < 10 ? item.distanceKm.toFixed(1) : Math.round(item.distanceKm)}km`
    : '';
  return [place, distance].filter(Boolean).join(' · ');
}

export default function ClassCard({
  item,
  isFavorite,
  isCompared,
  onToggleFavorite,
  onApply,
  onToggleCompare,
  onOpenDetails,
  onOpenLocation,
}: ClassCardProps) {
  const closed = item.statusCode === 'CLOSED' || /^(?:마감|접수마감)$/.test(item.status.trim());
  const applicationUrl = firstSafeExternalUrl(item.applicationUrl, item.rawUrl);
  const canOpenApplication = Boolean(applicationUrl);
  const tags = categoryTags(item);
  const age = hasUsefulCourseText(item.age) ? item.age.trim() : '연령 미정';
  const materialFee = materialFeeLabel(item);
  const actionLabel = closed || !canOpenApplication ? '상세보기' : '신청 페이지로';

  return (
    <article className={`class-card course-list-card ${closed ? 'is-closed' : ''}`}>
      <div className="course-card-overview">
        <div className={`course-card-thumbnail ${item.thumbnailClass}`}>
          <span className="course-card-image-fallback" aria-hidden="true">{item.thumbnailEmoji}</span>
          {item.imageUrl && (
            <img
              src={item.imageUrl}
              alt=""
              loading="lazy"
              decoding="async"
              referrerPolicy="no-referrer"
              onError={(event) => {
                event.currentTarget.hidden = true;
              }}
            />
          )}
          <span className={`status-badge status-${statusClass(item)}`}>{shortStatusLabel(item)}</span>
          <button
            className="course-thumbnail-open"
            type="button"
            aria-label={`${item.title} 상세보기`}
            onClick={() => onOpenDetails(item)}
          />
        </div>

        <div className="course-card-heading">
          <button className="course-title-button" type="button" onClick={() => onOpenDetails(item)}>
            <h3>{item.title}</h3>
          </button>
          <div className="course-card-tags" aria-label="강좌 분류와 대상">
            {tags.map((tag) => <span key={tag}>{tag}</span>)}
            <span className="course-age-tag">{age}</span>
          </div>
        </div>

        <div className="course-price">
          <strong>{formatCoursePrice(item)}</strong>
          {materialFee && <small className="course-material-fee">{materialFee}</small>}
        </div>

        <button
          className={`favorite-button ${isFavorite ? 'active' : ''}`}
          type="button"
          aria-label={`${item.title} ${isFavorite ? '찜 해제' : '찜하기'}`}
          aria-pressed={isFavorite}
          onClick={() => onToggleFavorite(item)}
        >
          <Heart size={21} strokeWidth={2} fill={isFavorite ? 'currentColor' : 'none'} aria-hidden="true" />
        </button>
      </div>

      <dl className="course-card-facts" aria-label="강좌 핵심 정보">
        <div>
          <dt><CalendarDays size={16} strokeWidth={1.9} aria-hidden="true" />수업</dt>
          <dd>{formatCourseSchedule(item)}</dd>
        </div>
        <div>
          <dt><ClipboardCheck size={16} strokeWidth={1.9} aria-hidden="true" />접수</dt>
          <dd>{formatApplicationPeriod(item)}</dd>
        </div>
        <div>
          <dt><MapPin size={16} strokeWidth={1.9} aria-hidden="true" />위치</dt>
          <dd>
            <button
              className="course-location-button"
              type="button"
              aria-haspopup="dialog"
              aria-label={`${locationLabel(item)} 지도에서 보기`}
              onClick={() => onOpenLocation(item)}
            >
              {locationLabel(item)}
            </button>
          </dd>
        </div>
      </dl>

      <div className="course-card-actions">
        <label className={`compare-check ${isCompared ? 'active' : ''}`}>
          <input
            type="checkbox"
            checked={isCompared}
            onChange={() => onToggleCompare(item)}
          />
          <span className="compare-check-icon" aria-hidden="true">
            {isCompared && <Check size={13} strokeWidth={3} />}
          </span>
          <span>비교 담기</span>
        </label>
        <button
          className="course-apply-action"
          type="button"
          onClick={() => {
            if (closed || !canOpenApplication) {
              onOpenDetails(item);
              return;
            }
            onApply(item);
          }}
          aria-label={canOpenApplication && !closed ? `${item.title} 신청 페이지 새 창으로 열기` : `${item.title} 상세보기`}
        >
          {actionLabel}
          {canOpenApplication && !closed && <ExternalLink size={16} strokeWidth={2} aria-hidden="true" />}
        </button>
      </div>
    </article>
  );
}
