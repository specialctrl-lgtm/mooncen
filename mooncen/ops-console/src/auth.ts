import { apiUrl, opsErrorFromResponse, opsFetch } from './api';

const CSRF_COOKIE_NAME = import.meta.env.VITE_OPS_CSRF_COOKIE_NAME || 'mooncen_ops_csrf';

export async function loginOpsWithPassword(loginId: string, password: string): Promise<void> {
  const response = await opsFetch(apiUrl('/api/auth/ops/login'), {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ login_id: loginId.trim(), password }),
  });
  if (!response.ok) {
    throw await opsErrorFromResponse(response, '아이디 또는 비밀번호를 확인해 주세요.');
  }
}

export async function logoutOpsSession(): Promise<void> {
  const prefix = `${CSRF_COOKIE_NAME}=`;
  const csrf = document.cookie
    .split(';')
    .map((item) => item.trim())
    .find((item) => item.startsWith(prefix));
  const response = await opsFetch(apiUrl('/api/auth/logout'), {
    method: 'POST',
    credentials: 'include',
    headers: csrf ? { 'X-CSRF-Token': decodeURIComponent(csrf.slice(prefix.length)) } : {},
  });
  if (!response.ok && response.status !== 401) {
    throw await opsErrorFromResponse(response, '로그아웃 요청에 실패했습니다.');
  }
}
