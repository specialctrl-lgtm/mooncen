export const RUNTIME_CONFIG_GLOBAL = '__MOONCEN_RUNTIME_CONFIG__' as const;

export type RuntimeConfig = Readonly<{
  siteUrl: string;
  oauthRedirectUri: string;
  kakaoMapsJavaScriptKey: string;
  googleOAuthClientId: string;
  naverOAuthClientId: string;
}>;

type RuntimeConfigSource = Partial<Record<keyof RuntimeConfig, unknown>>;

declare global {
  interface Window {
    readonly __MOONCEN_RUNTIME_CONFIG__?: Readonly<RuntimeConfigSource>;
  }
}

const MAX_PUBLIC_VALUE_LENGTH = 4096;

function hasUnsafeControlCharacter(value: string): boolean {
  return Array.from(value).some((character) => {
    const codePoint = character.codePointAt(0) ?? 0;
    return codePoint < 32 || codePoint === 127;
  });
}

function safePublicString(value: unknown): string {
  if (typeof value !== 'string') return '';
  if (hasUnsafeControlCharacter(value)) return '';
  const normalized = value.trim();
  if (
    normalized.length > MAX_PUBLIC_VALUE_LENGTH
  ) {
    return '';
  }
  return normalized;
}

function frozenWindowConfig(): RuntimeConfigSource | undefined {
  if (typeof window === 'undefined') return undefined;
  const candidate = window.__MOONCEN_RUNTIME_CONFIG__;
  if (
    !candidate
    || typeof candidate !== 'object'
    || Array.isArray(candidate)
    || !Object.isFrozen(candidate)
  ) {
    return undefined;
  }
  return candidate;
}

function publicValue(key: keyof RuntimeConfig, viteFallback: unknown): string {
  return safePublicString(frozenWindowConfig()?.[key]) || safePublicString(viteFallback);
}

// Getters keep Vite's development/test fallback behavior while the Docker image
// reads the immutable object installed by /runtime-config.js before main.tsx.
export const runtimeConfig: RuntimeConfig = Object.freeze({
  get siteUrl() {
    return publicValue('siteUrl', import.meta.env.VITE_SITE_URL);
  },
  get oauthRedirectUri() {
    return publicValue('oauthRedirectUri', import.meta.env.VITE_OAUTH_REDIRECT_URI);
  },
  get kakaoMapsJavaScriptKey() {
    return publicValue(
      'kakaoMapsJavaScriptKey',
      import.meta.env.VITE_KAKAO_MAPS_JAVASCRIPT_KEY,
    );
  },
  get googleOAuthClientId() {
    return publicValue('googleOAuthClientId', import.meta.env.VITE_GOOGLE_OAUTH_CLIENT_ID);
  },
  get naverOAuthClientId() {
    return publicValue('naverOAuthClientId', import.meta.env.VITE_NAVER_OAUTH_CLIENT_ID);
  },
});
