import { afterEach, describe, expect, it, vi } from 'vitest';
import { OPS_REQUEST_TIMEOUT_MS, OpsApiError, opsApi } from './api';

describe('opsApi', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
    document.cookie = 'mooncen_ops_csrf=; Max-Age=0; path=/';
  });

  it('uses credentialed requests and sends CSRF only for mutations', async () => {
    document.cookie = 'mooncen_ops_csrf=csrf-test-value; path=/';
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } }));

    await opsApi('/quality/scan', { method: 'POST', body: '{}' });
    const [, init] = fetchMock.mock.calls[0];
    const headers = new Headers(init?.headers);

    expect(init?.credentials).toBe('include');
    expect(headers.get('X-CSRF-Token')).toBe('csrf-test-value');
    expect(headers.get('Content-Type')).toBe('application/json');
  });

  it('returns a bounded backend error instead of a fake success', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: { message: 'Ops database migration has not been applied' } }), {
        status: 503,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    await expect(opsApi('/jobs')).rejects.toThrow('Ops database migration has not been applied');
  });

  it('surfaces the backend request id for support correlation', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Internal server error', request_id: 'request-body-id' }), {
        status: 500,
        headers: { 'Content-Type': 'application/json', 'X-Request-ID': 'request-header-id' },
      }),
    );

    const error = await opsApi('/jobs').catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(OpsApiError);
    expect((error as OpsApiError).status).toBe(500);
    expect((error as OpsApiError).requestId).toBe('request-body-id');
  });

  it('aborts a stalled request after the bounded timeout', async () => {
    vi.useFakeTimers();
    vi.spyOn(globalThis, 'fetch').mockImplementation((_input, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
    }));

    const request = opsApi('/jobs');
    const assertion = expect(request).rejects.toMatchObject({ kind: 'timeout', status: 0 });
    await vi.advanceTimersByTimeAsync(OPS_REQUEST_TIMEOUT_MS);
    await assertion;
  });

  it('classifies a transport failure separately from authentication', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('connection refused'));

    await expect(opsApi('/session')).rejects.toMatchObject({ kind: 'network', status: 0 });
  });

  it('notifies the app for an expired page request without recursively refetching session', async () => {
    const expired = vi.fn();
    window.addEventListener('mooncen:ops-auth-expired', expired);
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Missing token' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    await opsApi('/session').catch(() => undefined);
    expect(expired).not.toHaveBeenCalled();
    await opsApi('/jobs').catch(() => undefined);
    expect(expired).toHaveBeenCalledTimes(1);
    window.removeEventListener('mooncen:ops-auth-expired', expired);
  });
});
