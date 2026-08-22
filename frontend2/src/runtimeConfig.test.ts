import { afterEach, describe, expect, it, vi } from 'vitest';

import { runtimeConfig } from './runtimeConfig';

function installWindowConfig(value: unknown) {
  Object.defineProperty(window, '__MOONCEN_RUNTIME_CONFIG__', {
    configurable: true,
    value,
  });
}

afterEach(() => {
  delete (window as { __MOONCEN_RUNTIME_CONFIG__?: unknown }).__MOONCEN_RUNTIME_CONFIG__;
  vi.unstubAllEnvs();
});
describe('runtime public configuration', () => {
  it('reads only normalized values from the frozen window object', () => {
    installWindowConfig(Object.freeze({
      siteUrl: ' https://runtime.example.test/ ',
      oauthRedirectUri: 'https://runtime.example.test/oauth/callback',
      kakaoMapsJavaScriptKey: 'kakao-runtime-key',
      googleOAuthClientId: 'google-runtime-id',
      naverOAuthClientId: 'naver-runtime-id',
      unexpectedSecret: 'must-not-be-readable',
    }));
    vi.stubEnv('VITE_SITE_URL', 'https://vite.example.test');

    expect(Object.isFrozen(runtimeConfig)).toBe(true);
    expect(runtimeConfig).toMatchObject({
      siteUrl: 'https://runtime.example.test/',
      oauthRedirectUri: 'https://runtime.example.test/oauth/callback',
      kakaoMapsJavaScriptKey: 'kakao-runtime-key',
      googleOAuthClientId: 'google-runtime-id',
      naverOAuthClientId: 'naver-runtime-id',
    });
    expect('unexpectedSecret' in runtimeConfig).toBe(false);
  });

  it('falls back to Vite values when a window object is absent or mutable', () => {
    vi.stubEnv('VITE_SITE_URL', ' https://vite.example.test/ ');
    vi.stubEnv('VITE_GOOGLE_OAUTH_CLIENT_ID', 'vite-google-id');
    installWindowConfig({
      siteUrl: 'https://mutable.example.test',
      googleOAuthClientId: 'mutable-google-id',
    });

    expect(runtimeConfig.siteUrl).toBe('https://vite.example.test/');
    expect(runtimeConfig.googleOAuthClientId).toBe('vite-google-id');
  });

  it('rejects control characters and overlong public values', () => {
    vi.stubEnv('VITE_OAUTH_REDIRECT_URI', 'https://vite.example.test/callback');
    installWindowConfig(Object.freeze({
      oauthRedirectUri: 'https://runtime.example.test/\ncallback',
      kakaoMapsJavaScriptKey: 'x'.repeat(4097),
    }));

    expect(runtimeConfig.oauthRedirectUri).toBe('https://vite.example.test/callback');
    expect(runtimeConfig.kakaoMapsJavaScriptKey).toBe('');
  });
});
