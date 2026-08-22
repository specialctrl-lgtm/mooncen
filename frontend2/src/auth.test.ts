import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { clearStoredUser, getCsrfToken, startOAuthLogin, storeUser } from './auth';
import { privacyMembershipNotice } from './privacyNotice';


describe('browser auth storage', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    document.cookie = 'mooncen_csrf=; Max-Age=0; path=/';
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('reads the double-submit CSRF cookie', () => {
    document.cookie = 'mooncen_csrf=csrf-value; path=/';
    expect(getCsrfToken()).toBe('csrf-value');
  });

  it('stores only display user data, never an access token', () => {
    storeUser({ provider: 'email', name: 'MoonCen User', email: 'user@example.test' });
    expect(Object.keys(localStorage)).toEqual(['mooncen:auth-user']);
    clearStoredUser();
    expect(localStorage.length).toBe(0);
  });

  it('requests OAuth state with consent and the exact authoritative notice version', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        google_client_id: 'google-client-id',
        google_client_secret_configured: true,
      }), { status: 200, headers: { 'content-type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'stop after checking request' }), {
        status: 400,
        headers: { 'content-type': 'application/json' },
      }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(startOAuthLogin('google', privacyMembershipNotice.version)).rejects.toThrow(
      'stop after checking request',
    );

    const stateRequest = new URL(String(fetchMock.mock.calls[1][0]), 'https://mooncen.test');
    expect(stateRequest.pathname).toBe('/api/auth/oauth/state');
    expect(stateRequest.searchParams.get('privacy_consent')).toBe('true');
    expect(stateRequest.searchParams.get('privacy_notice_version')).toBe(privacyMembershipNotice.version);
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });
});
