import { useState } from 'react';
import type { ClassItem } from '../data/mockData';
import { useDialogAccessibility } from '../hooks/useDialogAccessibility';
import { firstSafeExternalUrl } from '../utils/safeUrl';
import ProviderIcon from './ProviderIcon';
import { normalizeCourseDisplayTitle } from '../utils/titleDisplay';

type CourseDetailModalProps = {
  item: ClassItem | null;
  isFavorite: boolean;
  isApplied: boolean;
  onClose: () => void;
  onAddMyCourse: (item: ClassItem) => void;
  onRemoveMyCourse: (item: ClassItem) => void;
  onApply: (item: ClassItem) => void;
  onToggleFavorite: (item: ClassItem) => void;
};

type DetailPanel = 'description' | 'notice' | null;
type CourseAction = {
  label: string;
  disabled: boolean;
  mode: 'apply' | 'notify' | 'closed';
};

const weekdays = ['일', '월', '화', '수', '목', '금', '토'];

function formatPrice(price: number) {
  return price > 0 ? `${price.toLocaleString('ko-KR')}원` : '확인 필요';
}

function formatOptionalPrice(price: number) {
  return price > 0 ? `${price.toLocaleString('ko-KR')}원` : '없음';
}

function formatTotalPrice(item: ClassItem) {
  const courseFee = item.price > 0 ? item.price : 0;
  const materialFee = item.materialFee > 0 ? item.materialFee : 0;
  const total = courseFee + materialFee;
  return total > 0 ? `${total.toLocaleString('ko-KR')}원` : '확인 필요';
}

function formatDate(value?: string | null) {
  if (!value) return '';
  const matched = value.match(/(\d{4})[-./](\d{1,2})[-./](\d{1,2})/);
  if (!matched) return value.replace(/-/g, '.');
  const year = Number(matched[1]);
  const month = Number(matched[2]);
  const day = Number(matched[3]);
  const date = new Date(year, month - 1, day);
  const weekday = Number.isNaN(date.getTime()) ? '' : ` (${weekdays[date.getDay()]})`;
  return `${year}.${String(month).padStart(2, '0')}.${String(day).padStart(2, '0')}${weekday}`;
}

function formatPlainDate(value?: string | null) {
  if (!value) return '';
  const matched = value.match(/(\d{4})[-./](\d{1,2})[-./](\d{1,2})/);
  if (!matched) return value.replace(/-/g, '.');
  return `${matched[1]}.${matched[2].padStart(2, '0')}.${matched[3].padStart(2, '0')}`;
}

function formatPeriod(item: ClassItem) {
  const start = formatPlainDate(item.startDate);
  const end = formatPlainDate(item.endDate);
  if (start && end && start !== end) {
    return start.slice(0, 4) === end.slice(0, 4) ? `${start} ~ ${end.slice(5)}` : `${start} ~ ${end}`;
  }
  return start || end || '';
}

function formatApplyPeriod(item: ClassItem) {
  if (item.applyPeriodRaw) return item.applyPeriodRaw;
  const start = formatPlainDate(item.applyStart);
  const end = formatPlainDate(item.applyEnd);
  if (start && end && start !== end) {
    return start.slice(0, 4) === end.slice(0, 4) ? `${start} ~ ${end.slice(5)}` : `${start} ~ ${end}`;
  }
  return start || end || '';
}

function formatScheduleDateList(item: ClassItem) {
  return item.scheduleDates.map((date) => formatDate(date)).join(', ');
}

function formatSessions(item: ClassItem) {
  if (item.scheduleDates.length > 1 && !item.sessions) return `${item.scheduleDates.length}회`;
  if (item.sessions && item.sessions > 0) return `${item.sessions}회`;
  if (item.sessionLabel) return item.sessionLabel;
  if (item.startDate && item.endDate && item.startDate === item.endDate) return '1회';
  return '횟수 미정';
}

function formatCapacity(item: ClassItem) {
  if (item.capacityRemaining != null) {
    return item.capacityTotal != null
      ? `${item.capacityRemaining.toLocaleString('ko-KR')}명 남음 / 정원 ${item.capacityTotal.toLocaleString('ko-KR')}명`
      : `${item.capacityRemaining.toLocaleString('ko-KR')}명 남음`;
  }
  if (item.capacityTotal != null) {
    const current = item.capacityCurrent != null ? ` · 신청 ${item.capacityCurrent.toLocaleString('ko-KR')}명` : '';
    return `정원 ${item.capacityTotal.toLocaleString('ko-KR')}명${current}`;
  }
  return '제공 정보 없음';
}

function statusTone(item: ClassItem) {
  const value = `${item.statusCode || ''} ${item.status || ''} ${item.statusLabel || ''}`;
  if (/DEADLINE|마감임박|임박/i.test(value)) return 'deadline';
  if (/CLOSED|마감/i.test(value)) return 'closed';
  if (/SCHEDULED|예정/i.test(value)) return 'scheduled';
  return 'open';
}

function brandShortLabel(item: ClassItem) {
  const source = item.source;
  const text = `${item.provider} ${item.providerLabel} ${item.center}`.toLowerCase();
  if (source === 'H' || text.includes('homeplus') || text.includes('홈플')) return '홈플';
  if (source === 'E' || text.includes('emart') || text.includes('이마트')) return '이마트';
  if (source === 'L' || text.includes('lotte') || text.includes('롯데')) return '롯데';
  if (source === 'M' || text.includes('롯데마트')) return '롯데마트';
  if (source === 'P' || text.includes('muni_') || text.includes('go.kr') || text.includes('평생학습')) return '공공기관';
  if (source === 'A' || text.includes('ak')) return 'AK';
  if (source === 'HD' || text.includes('hyundai') || text.includes('현대')) return '현대';
  if (source === 'S' || text.includes('shinsegae') || text.includes('신세계')) return '신세계';
  if (source === 'G' || text.includes('galleria') || text.includes('갤러리아')) return '갤러리아';
  return item.providerLabel || '문화센터';
}

function detailTitle(item: ClassItem) {
  const title = normalizeCourseDisplayTitle(item.title, '');
  if (title && title !== '강좌명 미정') return title;
  if (item.category && item.category !== '기타') return `${item.category} 강좌`;
  if (item.ageGroup && item.ageGroup !== '연령 미정') return `${item.ageGroup} 강좌`;
  return '문화센터 강좌';
}

function compactText(value?: string | null) {
  const text = (value || '').trim();
  return text && !/미정|확인 필요|없음|null|undefined/i.test(text) ? text : '제공 정보 없음';
}

function scheduleSummary(item: ClassItem) {
  const time = item.scheduleTime && item.scheduleTime !== '시간 미정' ? item.scheduleTime : '';
  const days = item.scheduleDays.filter(Boolean).join(', ');
  if (days && time) return `${days} ${time}`;
  if (time) return time;
  if (item.scheduleRaw) {
    const matched = item.scheduleRaw.match(/[월화수목금토일](?:\s*,\s*[월화수목금토일])?\s*\d{1,2}:\d{2}\s*[~-]\s*\d{1,2}:\d{2}/);
    if (matched) return matched[0].replace(/\s+/g, ' ');
  }
  return days || item.schedule || '일정 확인 필요';
}

function daysUntilDate(value?: string | null) {
  if (!value) return null;
  const matched = value.match(/(\d{4})[-./](\d{1,2})[-./](\d{1,2})/);
  if (!matched) return null;
  const target = new Date(Number(matched[1]), Number(matched[2]) - 1, Number(matched[3]));
  if (Number.isNaN(target.getTime())) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  target.setHours(0, 0, 0, 0);
  return Math.round((target.getTime() - today.getTime()) / 86_400_000);
}

function favoriteAlertBadges(item: ClassItem) {
  const badges: string[] = [];
  const openDays = daysUntilDate(item.applyStart);
  const closingDays = daysUntilDate(item.applyEnd);
  if (openDays != null && openDays >= 0 && openDays <= 1) {
    badges.push(openDays === 0 ? '접수 시작 오늘' : '접수 시작 내일');
  }
  if (closingDays != null && closingDays >= 0 && closingDays <= 1) {
    badges.push(closingDays === 0 ? '마감 오늘' : '마감 내일');
  }
  return badges;
}

function primaryCourseAction(item: ClassItem, hasApplication: boolean): CourseAction {
  const tone = statusTone(item);
  if (tone === 'closed') return { label: '마감', disabled: true, mode: 'closed' };
  if (tone === 'scheduled') return { label: '알림 신청', disabled: false, mode: 'notify' };
  return { label: '수강신청', disabled: !hasApplication, mode: 'apply' };
}

export default function CourseDetailModal({
  item,
  isFavorite,
  isApplied,
  onClose,
  onAddMyCourse,
  onRemoveMyCourse,
  onApply,
  onToggleFavorite,
}: CourseDetailModalProps) {
  const [detailPanel, setDetailPanel] = useState<DetailPanel>(null);
  const [shareDone, setShareDone] = useState(false);
  const dialogRef = useDialogAccessibility<HTMLElement>(Boolean(item), onClose);
  const extraDialogRef = useDialogAccessibility<HTMLElement>(Boolean(detailPanel), () => setDetailPanel(null));
  if (!item) return null;

  const title = detailTitle(item);
  const period = formatPeriod(item);
  const detailDescription = item.description || item.aiSummary || '';
  const hasApplication = Boolean(firstSafeExternalUrl(item.applicationUrl, item.rawUrl));
  const statusLabel = item.statusLabel || item.status || '상태 미정';
  const brand = brandShortLabel(item);
  const fullProvider = item.providerLabel || `${brand} 문화센터`;
  const totalFee = formatTotalPrice(item);
  const courseAction = primaryCourseAction(item, hasApplication);
  const applyPeriod = formatApplyPeriod(item);
  const alertBadges = isFavorite ? favoriteAlertBadges(item) : [];

  const handleShare = async () => {
    const shareUrl = window.location.href;
    try {
      if (navigator.share) {
        await navigator.share({ title, url: shareUrl });
      } else if (navigator.clipboard) {
        await navigator.clipboard.writeText(shareUrl);
      } else {
        throw new Error('공유 기능을 지원하지 않는 브라우저입니다.');
      }
      setShareDone(true);
      window.setTimeout(() => setShareDone(false), 1600);
    } catch {
      setShareDone(false);
    }
  };

  const detailRows = [
    { key: 'target', label: '대상', value: item.eligibilityRaw || item.age },
    { key: 'instructor', label: '강사', value: compactText(item.instructor) },
    { key: 'capacity', label: '정원', value: formatCapacity(item) },
    { key: 'category', label: '카테고리', value: compactText(item.category) },
    { key: 'material', label: '준비물', value: '수강신청 페이지 확인' },
    { key: 'materialFee', label: '재료비', value: formatOptionalPrice(item.materialFee) },
    { key: 'applyPeriod', label: '접수기간', value: applyPeriod || '제공 정보 없음' },
    { key: 'status', label: '모집상태', value: statusLabel },
    { key: 'location', label: '위치', value: item.center || '지점 정보 없음' },
  ];
  const summaryRows = [
    { key: 'schedule', icon: '시', label: '수업일시', value: scheduleSummary(item) },
    { key: 'period', icon: '기', label: '기간', value: period || '기간 확인 필요' },
    { key: 'apply', icon: '접', label: '접수', value: applyPeriod || '접수일 확인 필요' },
    { key: 'sessions', icon: '회', label: '횟수', value: formatSessions(item) },
    { key: 'price', icon: '원', label: '수강료', value: formatPrice(item.price), tone: 'price' },
  ];
  const extraRows = [
    { label: '환불 안내', value: '원문 기준 확인' },
    { label: '유의사항', value: '접수 조건 확인' },
    { label: '주차 안내', value: '지점별 상이' },
    { label: '문의', value: item.center || fullProvider },
  ];

  return (
    <div className="modal-backdrop course-detail-backdrop" role="presentation" onClick={onClose}>
      <section
        ref={dialogRef}
        className="course-detail-modal redesigned-course-detail"
        role="dialog"
        aria-modal="true"
        aria-labelledby="course-detail-title"
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="course-detail-header">
          <button className="course-detail-header-button" type="button" onClick={onClose} aria-label="뒤로가기">
            ←
          </button>
          <strong>강좌 상세</strong>
          <div className="course-detail-header-actions">
            <button className="course-detail-header-button" type="button" onClick={handleShare} aria-label="공유">
              {shareDone ? '✓' : '↗'}
            </button>
            <button
              className={`course-detail-header-button ${isFavorite ? 'active' : ''}`}
              type="button"
              onClick={() => onToggleFavorite(item)}
              aria-label={`${title} 찜하기`}
            >
              {isFavorite ? '♥' : '♡'}
            </button>
            <button className="course-detail-header-button course-detail-close-button" type="button" aria-label="닫기" onClick={onClose}>
              ×
            </button>
          </div>
        </header>

        <div className="course-detail-body" role="region" aria-label="강좌 상세 내용" tabIndex={0}>
          <section className="course-detail-left-column" aria-label="강좌 이미지와 비용">
            <div className={`course-detail-hero ${item.thumbnailClass}`}>
              {item.imageUrl ? (
                <img className="course-detail-image" src={item.imageUrl} alt={`${title} 이미지`} loading="lazy" decoding="async" referrerPolicy="no-referrer" />
              ) : (
                <span className="course-detail-emoji" aria-hidden="true">{item.thumbnailEmoji}</span>
              )}
              <span className={`status-badge detail-status status-${statusTone(item)}`}>{statusLabel}</span>
            </div>

            <section className="course-detail-fee-summary" aria-label="비용 정보">
              <div>
                <span>총 예상 비용</span>
                <strong>{totalFee}</strong>
              </div>
              <dl>
                <div>
                  <dt>수강료</dt>
                  <dd>{formatPrice(item.price)}</dd>
                </div>
                <div>
                  <dt>재료비</dt>
                  <dd>{formatOptionalPrice(item.materialFee)}</dd>
                </div>
              </dl>
              {item.materialFee > 0 && <p className="course-detail-material-note">※ 재료비는 센터 사정에 따라 변경될 수 있습니다.</p>}
            </section>

            {detailDescription && (
              <section className="course-detail-description-card" aria-label="강좌 소개 요약">
                <div className="course-detail-section-heading">
                  <h3>강좌 소개</h3>
                  <button type="button" onClick={() => setDetailPanel('description')}>
                    자세히 보기
                  </button>
                </div>
                <p>{detailDescription}</p>
              </section>
            )}
          </section>

          <section className="course-detail-right-column" aria-label="강좌 핵심 정보">
            <div className="course-detail-title-block">
              <div className="course-detail-provider-line">
                <ProviderIcon providerName={item.providerLabel || item.provider} providerType={item.provider} centerName={item.center} size="small" />
                <span className="course-detail-brand-chip">{brand}</span>
                <span>{fullProvider} {item.center}</span>
              </div>
              <h2 id="course-detail-title">{title}</h2>
              <div className="course-detail-tags">
                {[item.category, item.age, ...item.aiTags.slice(0, 2), statusTone(item) === 'deadline' ? '마감임박' : null].filter(Boolean).map((tag) => (
                  <span key={tag}>#{tag}</span>
                ))}
              </div>
              {alertBadges.length > 0 && (
                <div className="course-detail-alert-badges" aria-label="관심 강좌 알림">
                  {alertBadges.map((badge) => <span key={badge}>{badge}</span>)}
                </div>
              )}
            </div>

            <section className="course-detail-summary-card" aria-label="핵심 요약">
              {summaryRows.map((row) => (
                <div key={row.label} className={`course-detail-summary-${row.key}`}>
                  <span className="course-detail-summary-icon" aria-hidden="true">{row.icon}</span>
                  <span>{row.label}</span>
                  <strong className={row.tone === 'price' ? 'price' : undefined}>{row.value}</strong>
                </div>
              ))}
            </section>

            <dl className="course-detail-compact-list">
              {detailRows.map((row) => (
                <div key={row.key} className={`course-detail-info-${row.key}`}>
                  <dt>{row.label}</dt>
                  <dd>{row.value}</dd>
                </div>
              ))}
            </dl>

            <div className="course-detail-extra-row" aria-label="부가 정보">
              {extraRows.map((row) => (
                <button key={row.label} type="button" onClick={() => setDetailPanel('notice')}>
                  <strong>{row.label}</strong>
                  <small>{row.value}</small>
                </button>
              ))}
            </div>
          </section>
        </div>

        <footer className="course-detail-footer">
          <button className={`detail-favorite-action ${isFavorite ? 'active' : ''}`} type="button" onClick={() => onToggleFavorite(item)}>
            {isFavorite ? '찜 해제' : '찜하기'}
          </button>
          <button
            className={`detail-my-course-action ${isApplied ? 'active' : ''}`}
            type="button"
            onClick={() => (isApplied ? onRemoveMyCourse(item) : onAddMyCourse(item))}
          >
            {isApplied ? '내 강좌 취소' : '내 강좌 등록'}
          </button>
          <button
            className="detail-source-action"
            type="button"
            disabled={courseAction.disabled}
            onClick={() => (courseAction.mode === 'notify' ? onAddMyCourse(item) : onApply(item))}
          >
            {courseAction.label}
          </button>
        </footer>

        {detailPanel && (
          <div className="course-detail-extra-backdrop" role="presentation" onClick={() => setDetailPanel(null)}>
            <section
              ref={extraDialogRef}
              className="course-detail-extra-modal"
              role="dialog"
              aria-modal="true"
              aria-label={detailPanel === 'description' ? '강좌 소개 전체 보기' : '부가 정보'}
              tabIndex={-1}
              onClick={(event) => event.stopPropagation()}
            >
              <header>
                <strong>{detailPanel === 'description' ? '강좌 소개' : '부가 정보'}</strong>
                <button type="button" onClick={() => setDetailPanel(null)} aria-label="닫기">×</button>
              </header>
              {detailPanel === 'description' ? (
                <div className="course-detail-extra-content">
                  <p>{detailDescription || '강좌 소개가 아직 등록되지 않았습니다.'}</p>
                  {item.scheduleDates.length > 1 && (
                    <>
                      <h4>수업일</h4>
                      <p>{formatScheduleDateList(item)}</p>
                    </>
                  )}
                </div>
              ) : (
                <div className="course-detail-extra-content course-detail-notice-grid">
                  <div>
                    <h4>환불 안내</h4>
                    <p>환불 기준은 각 문화센터의 수강신청 페이지에서 확인하세요.</p>
                  </div>
                  <div>
                    <h4>유의사항</h4>
                    <p>준비물, 동반 가능 여부, 접수 조건은 원문 신청 페이지 기준으로 확인하세요.</p>
                  </div>
                  <div>
                    <h4>주차 안내</h4>
                    <p>주차 지원 여부는 지점별로 다를 수 있습니다.</p>
                  </div>
                  <div>
                    <h4>문의</h4>
                    <p>{fullProvider} {item.center}</p>
                  </div>
                </div>
              )}
            </section>
          </div>
        )}
      </section>
    </div>
  );
}
