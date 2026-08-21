import type { AuthUser } from '../auth';
import { useDialogAccessibility } from '../hooks/useDialogAccessibility';

type AccountModalProps = {
  open: boolean;
  user: AuthUser | null;
  deleting: boolean;
  onClose: () => void;
  onDeleteAccount: () => void;
};

function providerLabel(provider?: string) {
  if (provider === 'google') return 'Google';
  if (provider === 'naver') return '네이버';
  if (provider === 'email') return '이메일';
  return '알 수 없음';
}

export default function AccountModal({ open, user, deleting, onClose, onDeleteAccount }: AccountModalProps) {
  const dialogRef = useDialogAccessibility<HTMLElement>(open, onClose);
  if (!open) return null;

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        ref={dialogRef}
        className="account-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="account-modal-title"
        aria-busy={deleting}
        tabIndex={-1}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="account-modal-header">
          <div>
            <h2 id="account-modal-title">내정보</h2>
            <p>로그인 정보와 계정 메뉴를 관리합니다.</p>
          </div>
          <button type="button" aria-label="닫기" onClick={onClose}>×</button>
        </header>

        <dl className="account-info-list">
          <div>
            <dt>이름</dt>
            <dd>{user?.name || '사용자'}</dd>
          </div>
          <div>
            <dt>이메일</dt>
            <dd>{user?.email || '-'}</dd>
          </div>
          <div>
            <dt>가입 방식</dt>
            <dd>{providerLabel(user?.provider)}</dd>
          </div>
        </dl>

        <section className="account-danger-zone" aria-labelledby="account-delete-title">
          <div>
            <h3 id="account-delete-title">회원 탈퇴</h3>
            <p>저장한 강좌, 알림 설정, 로그인 연결 정보가 함께 삭제됩니다.</p>
          </div>
          <button type="button" onClick={onDeleteAccount} disabled={deleting}>
            {deleting ? '처리 중' : '탈퇴'}
          </button>
        </section>
      </section>
    </div>
  );
}
