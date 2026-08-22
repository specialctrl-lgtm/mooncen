import type { UserNotification } from '../api';
import { useDialogAccessibility } from '../hooks/useDialogAccessibility';

type NotificationPanelProps = {
  open: boolean;
  loading: boolean;
  notifications: UserNotification[];
  onClose: () => void;
  onRefresh: () => void;
  onOpenCourse: (notification: UserNotification) => void;
};

function formatDate(value?: string | null) {
  if (!value) return '';
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString('ko-KR', { month: 'long', day: 'numeric', weekday: 'short' });
}

function typeLabel(notification: UserNotification) {
  if (notification.mark_type === 'applied') return '내강좌';
  if (notification.notification_type === 'DEADLINE') return '마감';
  if (notification.notification_type === 'START') return '예정';
  return '찜';
}

export default function NotificationPanel({
  open,
  loading,
  notifications,
  onClose,
  onRefresh,
  onOpenCourse,
}: NotificationPanelProps) {
  const dialogRef = useDialogAccessibility<HTMLElement>(open, onClose);
  if (!open) return null;

  return (
    <div className="notification-panel-backdrop" role="presentation" onClick={onClose}>
      <section
        ref={dialogRef}
        className="notification-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="notification-panel-title"
        aria-busy={loading}
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="notification-panel-header">
          <div>
            <h2 id="notification-panel-title">알림</h2>
            <p>찜한 강좌 접수와 내 강좌 일정을 확인합니다.</p>
          </div>
          <div className="notification-panel-actions">
            <button type="button" onClick={onRefresh}>새로고침</button>
            <button type="button" aria-label="알림 닫기" onClick={onClose}>닫기</button>
          </div>
        </div>

        {loading ? (
          <div className="notification-empty" role="status" aria-live="polite">알림을 불러오는 중입니다.</div>
        ) : notifications.length ? (
          <ul className="notification-list">
            {notifications.map((notification) => (
              <li key={notification.id}>
                <button type="button" onClick={() => onOpenCourse(notification)}>
                  <span className={`notification-type type-${notification.notification_type.toLowerCase()}`}>
                    {typeLabel(notification)}
                  </span>
                  <strong>{notification.title}</strong>
                  <span>{notification.message}</span>
                  {notification.event_date && <time>{formatDate(notification.event_date)}</time>}
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <div className="notification-empty">
            지금 확인할 알림이 없습니다.
          </div>
        )}
      </section>
    </div>
  );
}
