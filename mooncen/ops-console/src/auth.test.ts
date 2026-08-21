import { afterEach, describe, expect, it, vi } from 'vitest';
import { loginOpsWithPassword } from './auth';
import { OpsApiError } from './api';

describe('dedicated Ops password login', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('submits only the standalone login id and password to the Ops endpoint', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ user: { id: '1' } }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    await loginOpsWithPassword(' opsadmin ', 'secret-password-value');

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/auth/ops/login');
    expect(init?.credentials).toBe('include');
    expect(JSON.parse(String(init?.body))).toEqual({
      login_id: 'opsadmin',
      password: 'secret-password-value',
    });
  });

  it('does not turn a rejected login into a local session', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Invalid Ops id or password' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    await expect(loginOpsWithPassword('opsadmin', 'wrong-password-value')).rejects.toThrow(
      'Invalid Ops id or password',
    );
  });

  it('keeps an API outage distinct from invalid credentials and retains request id', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Internal server error', request_id: 'ops-login-request' }), {
        status: 503,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    const error = await loginOpsWithPassword('opsadmin', 'secret-password-value').catch(
      (caught: unknown) => caught,
    );

    expect(error).toBeInstanceOf(OpsApiError);
    expect(error).toMatchObject({ status: 503, requestId: 'ops-login-request' });
  });
});
