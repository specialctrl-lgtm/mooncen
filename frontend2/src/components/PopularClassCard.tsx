import type { PopularClassItem } from '../data/mockData';

type PopularClassCardProps = {
  item: PopularClassItem;
  onSelect: (item: PopularClassItem) => void;
};

export default function PopularClassCard({ item, onSelect }: PopularClassCardProps) {
  const isClosed = item.statusCode === 'CLOSED' || item.status === '마감';

  return (
    <button className={`popular-card popular-card-button ${isClosed ? 'is-closed' : ''}`} type="button" onClick={() => onSelect(item)}>
      <div className="rank-badge">{item.rank}</div>
      <div className={`popular-thumb ${item.thumbnailClass}`}>
        {item.imageUrl ? (
          <img className="course-image" src={item.imageUrl} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer" />
        ) : (
          <span aria-hidden="true">{item.thumbnailEmoji}</span>
        )}
      </div>
      <div className="popular-copy">
        <h3 className={isClosed ? 'closed-title' : undefined}>{item.title}</h3>
        <p className="popular-age-text">대상 <strong>{item.age}</strong></p>
        <p className="popular-branch-text">
          <span>{item.center}</span>
          <em className={`provider-badge source-${item.source.toLowerCase()}`}>{item.providerLabel}</em>
        </p>
        <p className="popular-category-text">카테고리 {item.category}</p>
        <p className="popular-schedule-lines">
          <span>{item.scheduleDate}</span>
          <span>{item.scheduleTime}{item.sessionLabel ? ` · ${item.sessionLabel}` : ''}</span>
        </p>
        <strong>{item.price > 0 ? `${item.price.toLocaleString('ko-KR')}원` : '수강료 확인'}</strong>
      </div>
    </button>
  );
}
