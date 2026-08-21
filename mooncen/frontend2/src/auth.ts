import { fetchWithTimeout } from './utils/fetchWithTimeout';
import { runtimeConfig } from './runtimeConfig';

export type AuthProvider = 'google' | 'naver' | 'email';

export type AuthUser = {
  id?: string;
  provider: AuthProvider;
  name: string;
  email?: string;
  accessToken?: string;
  code?: string;
};

const USER_KEY = 'mooncen:auth-user';
const STATE_KEY = 'mooncen:oauth-state';
const CSRF_COOKIE = 'mooncen_csrf';

type OAuthProvider = Exclude<AuthProvider, 'email'>;

type StoredOAuthState = {
  provider: OAuthProvider;
  redirectUri: string;
  state: string;
};

function providerDisplayName(provider: AuthProvider) {
  if (provider === 'naver') return '네이버 사용자';
  if (provider === 'google') return 'Google 사용자';
  return '사용자';
}

export function normalizeAuthUser(user: AuthUser): AuthUser {
  const name = (user.name || '').trim();
  const lowerName = name.toLowerCase();
  if (!name || lowerName === user.provider || lowerName === `${user.provider} 사용자`) {
    return { ...user, name: providerDisplayName(user.provider) };
  }
  return { ...user, name };
}

function storeExpectedState(value: StoredOAuthState) {
  sessionStorage.setItem(STATE_KEY, JSON.stringify(value));
}

function readExpectedState(): StoredOAuthState | null {
  try {
    const raw = sessionStorage.getItem(STATE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredOAuthState>;
    if (
      (parsed.provider !== 'google' && parsed.provider !== 'naver')
      || typeof parsed.redirectUri !== 'string'
      || typeof parsed.state !== 'string'
    ) {
      return null;
    }
    return parsed as StoredOAuthState;
  } catch {
    return null;
  }
}

function clearExpectedState() {
  sessionStorage.removeItem(STATE_KEY);
}

function getRedirectUri() {
  const configuredRedirectUri = runtimeConfig.oauthRedirectUri;
  if (configuredRedirectUri) return configuredRedirectUri;

  const configuredSiteUrl = runtimeConfig.siteUrl;
  if (configuredSiteUrl) {
    return `${configuredSiteUrl.replace(/\/+$/, '')}/`;
  }

  return `${window.location.origin}/`;
}

export function getStoredUser(): AuthUser | null {
  try {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? normalizeAuthUser(JSON.parse(raw) as AuthUser) : null;
  } catch {
    return null;
  }
}

export function storeUser(user: AuthUser) {
  localStorage.setItem(USER_KEY, JSON.stringify(normalizeAuthUser(user)));
}

export function getCsrfToken(): string {
  const prefix = `${CSRF_COOKIE}=`;
  const value = document.cookie.split(';').map((part) => part.trim()).find((part) => part.startsWith(prefix));
  return value ? decodeURIComponent(value.slice(prefix.length)) : '';
}

export function clearStoredUser() {
  localStorage.removeItem(USER_KEY);
}

export async function deleteStoredAccount(): Promise<void> {
  const response = await fetchWithTimeout('/api/auth/me', {
    method: 'DELETE',
    credentials: 'include',
    headers: {
      'X-CSRF-Token': getCsrfToken(),
    },
  });

  if (!response.ok) {
    if (response.status === 401) clearStoredUser();
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || '회원 탈퇴 처리에 실패했습니다.');
  }

  clearStoredUser();
}

export async function refreshStoredUser(): Promise<AuthUser | null> {
  const response = await fetchWithTimeout('/api/auth/me', {
    credentials: 'include',
  });

  if (!response.ok) {
    if (response.status === 401) clearStoredUser();
    return null;
  }

  const user = normalizeAuthUser((await response.json()) as AuthUser);
  storeUser(user);
  return user;
}

type AuthResponse = {
  user: AuthUser;
};

async function authRequest(path: string, body: unknown): Promise<AuthUser> {
  const response = await fetchWithTimeout(path, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || '인증 요청에 실패했습니다.');
  }

  const data = (await response.json()) as AuthResponse;
  storeUser(data.user);
  return data.user;
}

export async function logoutStoredUser(): Promise<void> {
  try {
    const response = await fetchWithTimeout('/api/auth/logout', {
      method: 'POST',
      credentials: 'include',
      headers: { 'X-CSRF-Token': getCsrfToken() },
    });
    if (!response.ok && response.status !== 401) {
      throw new Error('로그아웃 요청에 실패했습니다.');
    }
  } finally {
    clearStoredUser();
  }
}

export function getMissingProviderConfig(provider: AuthProvider) {
  if (provider === 'google' && !runtimeConfig.googleOAuthClientId) {
    return 'VITE_GOOGLE_OAUTH_CLIENT_ID';
  }
  if (provider === 'naver' && !runtimeConfig.naverOAuthClientId) {
    return 'VITE_NAVER_OAUTH_CLIENT_ID';
  }
  return null;
}

type OAuthConfig = {
  google_client_id?: string;
  google_client_secret_configured?: boolean;
  naver_client_id?: string;
  naver_client_secret_configured?: boolean;
};

type OAuthStartResult = {
  started: boolean;
  missingConfig?: string;
};

type OAuthStateResponse = {
  state: string;
  code_challenge?: string;
  code_challenge_method?: 'S256';
};

function providerConfigNames(provider: AuthProvider) {
  return provider === 'google'
    ? { clientId: 'GOOGLE_OAUTH_CLIENT_ID', clientSecret: 'GOOGLE_OAUTH_CLIENT_SECRET' }
    : { clientId: 'NAVER_OAUTH_CLIENT_ID', clientSecret: 'NAVER_OAUTH_CLIENT_SECRET' };
}

function envOAuthClientId(provider: AuthProvider) {
  return provider === 'google'
    ? runtimeConfig.googleOAuthClientId
    : runtimeConfig.naverOAuthClientId;
}

async function fetchOAuthConfig(): Promise<OAuthConfig | null> {
  try {
    const response = await fetchWithTimeout('/api/auth/oauth/config');
    if (!response.ok) return null;
    return (await response.json()) as OAuthConfig;
  } catch {
    return null;
  }
}

function configOAuthClientId(provider: AuthProvider, config: OAuthConfig | null) {
  if (!config) return '';
  return provider === 'google' ? config.google_client_id || '' : config.naver_client_id || '';
}

function isProviderSecretConfigured(provider: AuthProvider, config: OAuthConfig | null) {
  if (!config) return true;
  return provider === 'google'
    ? config.google_client_secret_configured !== false
    : config.naver_client_secret_configured !== false;
}

async function fetchOAuthState(provider: OAuthProvider, redirectUri: string, privacyNoticeVersion: string) {
  const params = new URLSearchParams({
    provider,
    redirect_uri: redirectUri,
    privacy_consent: 'true',
    privacy_notice_version: privacyNoticeVersion,
  });
  const response = await fetchWithTimeout(`/api/auth/oauth/state?${params.toString()}`, {
    credentials: 'include',
    headers: { Accept: 'application/json' },
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || 'OAuth 로그인 상태를 준비하지 못했습니다. 잠시 후 다시 시도해 주세요.');
  }

  const data = (await response.json()) as OAuthStateResponse;
  if (!data.state || data.state.length < 16) {
    throw new Error('OAuth 로그인 상태 응답이 올바르지 않습니다.');
  }
  if (
    provider === 'google'
    && (!data.code_challenge || data.code_challenge_method !== 'S256')
  ) {
    throw new Error('Google OAuth PKCE 설정을 준비하지 못했습니다.');
  }
  return data;
}

export async function startOAuthLogin(
  provider: AuthProvider,
  privacyNoticeVersion: string,
): Promise<OAuthStartResult> {
  if (provider !== 'google' && provider !== 'naver') {
    return { started: false };
  }
  const redirectUri = getRedirectUri();
  const config = await fetchOAuthConfig();
  const clientId = envOAuthClientId(provider) || configOAuthClientId(provider, config);
  const configNames = providerConfigNames(provider);

  if (!clientId) {
    return { started: false, missingConfig: configNames.clientId };
  }

  if (!isProviderSecretConfigured(provider, config)) {
    return { started: false, missingConfig: configNames.clientSecret };
  }

  const oauthState = await fetchOAuthState(provider, redirectUri, privacyNoticeVersion);
  const state = oauthState.state;
  storeExpectedState({ provider, redirectUri, state });

  if (provider === 'google') {
    const params = new URLSearchParams({
      client_id: clientId,
      redirect_uri: redirectUri,
      response_type: 'code',
      scope: 'openid email profile',
      prompt: 'select_account',
      state,
    });
    if (oauthState.code_challenge && oauthState.code_challenge_method === 'S256') {
      params.set('code_challenge', oauthState.code_challenge);
      params.set('code_challenge_method', oauthState.code_challenge_method);
    }
    window.location.assign(`https://accounts.google.com/o/oauth2/v2/auth?${params.toString()}`);
    return { started: true };
  }

  const params = new URLSearchParams({
    response_type: 'code',
    client_id: clientId,
    redirect_uri: redirectUri,
    state,
  });
  window.location.assign(`https://nid.naver.com/oauth2.0/authorize?${params.toString()}`);
  return { started: true };
}

export async function finishOAuthLoginFromUrl(): Promise<AuthUser | null> {
  const expectedState = readExpectedState();
  const query = new URLSearchParams(window.location.search);
  const oauthError = query.get('error');
  if (oauthError) {
    const description = query.get('error_description') || query.get('error_message') || oauthError;
    clearExpectedState();
    window.history.replaceState({}, document.title, window.location.pathname);
    throw new Error(`OAuth 로그인 실패: ${description}`);
  }

  const code = query.get('code');
  const state = query.get('state');
  if (code && state && expectedState && state === expectedState.state) {
    try {
      return await authRequest(`/api/auth/oauth/${expectedState.provider}`, {
        code,
        state,
        redirect_uri: expectedState.redirectUri,
      });
    } finally {
      clearExpectedState();
      window.history.replaceState({}, document.title, window.location.pathname);
    }
  }

  if (code || state) {
    clearExpectedState();
    window.history.replaceState({}, document.title, window.location.pathname);
    throw new Error('OAuth 로그인 상태를 확인할 수 없습니다. 다시 로그인해 주세요.');
  }

  return null;
}
