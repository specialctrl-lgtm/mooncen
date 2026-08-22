import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { privacyMembershipNotice } from '../privacyNotice';
import LoginModal from './LoginModal';

afterEach(() => cleanup());

function renderModal() {
  const onClose = vi.fn();
  const onLogin = vi.fn();
  const view = render(
    <LoginModal open missingConfig={null} onClose={onClose} onLogin={onLogin} />,
  );
  return { ...view, onClose, onLogin };
}

describe('LoginModal privacy consent', () => {
  it('shows the authoritative membership privacy notice', () => {
    renderModal();

    expect(screen.getByText('처음 이용하는 소셜 계정은 회원가입이 함께 진행됩니다.')).toBeTruthy();
    expect(screen.getByText(privacyMembershipNotice.purpose)).toBeTruthy();
    privacyMembershipNotice.items.forEach((item) => expect(screen.getByText(item)).toBeTruthy());
    expect(screen.getByText(privacyMembershipNotice.retention)).toBeTruthy();
    expect(screen.getByText(privacyMembershipNotice.refusal)).toBeTruthy();
  });

  it('enables social login only after labeled consent and passes the exact notice version', () => {
    const { onLogin } = renderModal();
    const consent = screen.getByRole('checkbox', { name: privacyMembershipNotice.consent_label });
    const google = screen.getByRole('button', { name: 'Google로 로그인' }) as HTMLButtonElement;
    const naver = screen.getByRole('button', { name: 'Naver로 로그인' }) as HTMLButtonElement;

    expect((consent as HTMLInputElement).required).toBe(true);
    expect(google.disabled).toBe(true);
    expect(naver.disabled).toBe(true);

    fireEvent.click(consent);
    expect(google.disabled).toBe(false);
    expect(naver.disabled).toBe(false);

    fireEvent.click(google);
    fireEvent.click(naver);
    expect(onLogin).toHaveBeenNthCalledWith(1, 'google', privacyMembershipNotice.version);
    expect(onLogin).toHaveBeenNthCalledWith(2, 'naver', privacyMembershipNotice.version);
  });

  it('resets consent after the modal closes and reopens', () => {
    const { rerender } = renderModal();
    fireEvent.click(screen.getByRole('checkbox', { name: privacyMembershipNotice.consent_label }));

    rerender(<LoginModal open={false} missingConfig={null} onClose={vi.fn()} onLogin={vi.fn()} />);
    rerender(<LoginModal open missingConfig={null} onClose={vi.fn()} onLogin={vi.fn()} />);

    const consent = screen.getByRole('checkbox', { name: privacyMembershipNotice.consent_label }) as HTMLInputElement;
    expect(consent.checked).toBe(false);
    expect((screen.getByRole('button', { name: 'Google로 로그인' }) as HTMLButtonElement).disabled).toBe(true);
  });
});
