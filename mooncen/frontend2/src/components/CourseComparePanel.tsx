import { useState } from 'react';
import { Columns3, Trash2, X } from 'lucide-react';
import type { ClassItem } from '../data/mockData';
import { formatCoursePrice, formatCourseSchedule } from '../utils/courseCardDisplay';

type CourseComparePanelProps = {
  items: ClassItem[];
  onRemove: (id: string) => void;
  onClear: () => void;
  onOpenDetails: (item: ClassItem) => void;
};

function useful(value?: string | null) {
  return value && !/(미정|확인 필요|null|undefined)/i.test(value) ? value : '정보 없음';
}

export default function CourseComparePanel({ items, onRemove, onClear, onOpenDetails }: CourseComparePanelProps) {
  const [expanded, setExpanded] = useState(false);
  if (!items.length) return null;

  const rows = [
    { label: '대상', value: (item: ClassItem) => useful(item.age) },
    { label: '수업', value: formatCourseSchedule },
    { label: '위치', value: (item: ClassItem) => useful(item.center) },
    { label: '수강료', value: formatCoursePrice },
    { label: '접수 상태', value: (item: ClassItem) => useful(item.statusLabel || item.status) },
  ];

  return (
    <section className={`course-compare-dock ${expanded ? 'is-expanded' : ''}`} aria-label="선택 강좌 비교" aria-live="polite">
      {expanded && (
        <div className="course-compare-popover">
          <header>
            <div>
              <span>선택 강좌 비교</span>
              <strong>{items.length}개 강좌</strong>
            </div>
            <button type="button" aria-label="비교표 닫기" onClick={() => setExpanded(false)}>
              <X size={19} strokeWidth={2} aria-hidden="true" />
            </button>
          </header>
          <div className="course-compare-table-wrap">
            <table className="course-compare-table">
              <thead>
                <tr>
                  <th scope="col">항목</th>
                  {items.map((item) => (
                    <th key={item.id} scope="col">
                      <button type="button" className="compare-title-button" onClick={() => onOpenDetails(item)}>
                        <span>{item.title}</span>
                        <small>{item.center}</small>
                      </button>
                      <button
                        type="button"
                        className="compare-remove-button"
                        aria-label={`${item.title} 비교에서 제거`}
                        onClick={() => onRemove(item.id)}
                      >
                        <X size={14} strokeWidth={2} aria-hidden="true" />
                      </button>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.label}>
                    <th scope="row">{row.label}</th>
                    {items.map((item) => (
                      <td key={`${row.label}:${item.id}`}>{row.value(item)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="course-compare-dock-inner">
        <div className="compare-selected-courses">
          {items.map((item) => (
            <div className="compare-selected-course" key={item.id}>
              <button type="button" className="compare-selected-open" onClick={() => onOpenDetails(item)}>
                <span className={`compare-selected-thumb ${item.thumbnailClass}`}>
                  <span aria-hidden="true">{item.thumbnailEmoji}</span>
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
                </span>
                <span>{item.title}</span>
              </button>
              <button
                type="button"
                className="compare-selected-remove"
                aria-label={`${item.title} 비교에서 제거`}
                onClick={() => onRemove(item.id)}
              >
                <X size={14} strokeWidth={2} aria-hidden="true" />
              </button>
            </div>
          ))}
        </div>

        <p><strong>{items.length}개</strong> 강좌 선택</p>

        <div className="course-compare-dock-actions">
          <button type="button" className="compare-clear" aria-label="비교 목록 비우기" onClick={onClear}>
            <Trash2 size={18} strokeWidth={2} aria-hidden="true" />
          </button>
          <button
            type="button"
            className="compare-open"
            disabled={items.length < 2}
            onClick={() => setExpanded((value) => !value)}
          >
            <Columns3 size={18} strokeWidth={2} aria-hidden="true" />
            비교하기
          </button>
        </div>
      </div>
    </section>
  );
}
