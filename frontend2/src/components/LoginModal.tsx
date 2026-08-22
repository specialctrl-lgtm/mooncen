import { useEffect, useState } from 'react';
import type { AuthProvider } from '../auth';
import { useDialogAccessibility } from '../hooks/useDialogAccessibility';
import { privacyMembershipNotice } from '../privacyNotice';

type LoginModalProps = {
  open: boolean;
  missingConfig: string | null;
  onClose: () => void;
  onLogin: (provider: AuthProvider, privacyNoticeVersion: string) => void;
};

export default function LoginModal({ open, missingConfig, onClose, onLogin }: LoginModalProps) {
  const [privacyConsent, setPrivacyConsent] = useState(false);
  const dialogRef = useDialogAccessibility<HTMLElement>(open, onClose);

  useEffect(() => {
    setPrivacyConsent(false);
  }, [open]);

  if (!open) return null;

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <section
        ref={dialogRef}
        className="login-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="login-title"
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="login-modal-header">
          <div>
            <h2 id="login-title">로그인</h2>
            <p>Google 또는 Naver 계정으로 시작합니다.</p>
            <p id="login-registration-notice">처음 이용하는 소셜 계정은 회원가입이 함께 진행됩니다.</p>
          </div>
          <button type="button" aria-label="닫기" onClick={onClose}>
            x
          </button>
        </div>

        <section className="login-privacy-notice" aria-labelledby="login-privacy-title">
          <h3 id="login-privacy-title">{privacyMembershipNotice.title}</h3>
          <dl>
            <div>
              <dt>수집·이용 목적</dt>
              <dd>{privacyMembershipNotice.purpose}</dd>
            </div>
            <div>
              <dt>수집 항목</dt>
              <dd>
                <ul>
                  {privacyMembershipNotice.items.map((item) => <li key={item}>{item}</li>)}
                </ul>
              </dd>
            </div>
            <div>
              <dt>보유·이용 기간</dt>
              <dd>{privacyMembershipNotice.retention}</dd>
            </div>
            <div>
              <dt>동의 거부 권리 및 불이익</dt>
              <dd>{privacyMembershipNotice.refusal}</dd>
            </div>
          </dl>
        </section>

        <label className="login-privacy-consent">
          <input
            type="checkbox"
            required
            aria-required="true"
            checked={privacyConsent}
            aria-describedby="login-registration-notice login-privacy-title"
            onChange={(event) => setPrivacyConsent(event.currentTarget.checked)}
          />
          <span>{privacyMembershipNotice.consent_label}</span>
        </label>

        <div className="login-actions">
          <button
            className="social-login google"
            type="button"
            disabled={!privacyConsent}
            onClick={() => onLogin('google', privacyMembershipNotice.version)}
          >
            <span aria-hidden="true">G</span>
            Google로 로그인
          </button>
          <button
            className="social-login naver"
            type="button"
            disabled={!privacyConsent}
            onClick={() => onLogin('naver', privacyMembershipNotice.version)}
          >
            <span aria-hidden="true">N</span>
            Naver로 로그인
          </button>
        </div>

        {missingConfig && (
          <p className="login-warning" role="alert">
            환경변수 <strong>{missingConfig}</strong> 설정이 필요합니다.
          </p>
        )}
      </section>
    </div>
  );
}
