import { afterEach, describe, expect, it, vi } from 'vitest';

import { fetchCourses, fetchNearbyBranches, submitBugReport } from './api';


describe('course list API contract', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('sends the server-side ordering together with every filtered page', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ total: 0, page: 1, size: 40, items: [] }),
      { status: 200, headers: { 'content-type': 'application/json' } },
    ));
    vi.stubGlobal('fetch', fetchMock);

    await fetchCourses({
      page: 1,
      size: 40,
      collectionCategories: ['교육'],
      statuses: ['OPEN'],
      sort: 'price_asc',
    });

    const requested = new URL(String(fetchMock.mock.calls[0][0]), 'https://mooncen.test');
    expect(requested.pathname).toBe('/api/courses/');
    expect(requested.searchParams.get('collection_category')).toBe('교육');
    expect(requested.searchParams.get('statuses')).toBe('OPEN');
    expect(requested.searchParams.get('sort')).toBe('price_asc');
  });
});

describe('nearby branch API contract', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('keeps the bounded wide-area request alive beyond the default API timeout', async () => {
    vi.useFakeTimers();
    let requestSignal: AbortSignal | undefined;
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      requestSignal = init?.signal ?? undefined;
      return new Promise<Response>((_resolve, reject) => {
        requestSignal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
      });
    });
    vi.stubGlobal('fetch', fetchMock);

    const request = fetchNearbyBranches(37.5665, 126.978, 30);
    const timeoutExpectation = expect(request).rejects.toMatchObject({ name: 'RequestTimeoutError' });
    await vi.advanceTimersByTimeAsync(12_001);
    expect(requestSignal?.aborted).toBe(false);

    await vi.advanceTimersByTimeAsync(17_999);
    await timeoutExpectation;
    expect(requestSignal?.aborted).toBe(true);
  });
});

describe('bug report API contract', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    document.cookie = 'mooncen_csrf=; Max-Age=0; path=/';
  });

  it('posts the authenticated JSON payload to the bug report endpoint', async () => {
    document.cookie = 'mooncen_csrf=csrf-test; path=/';
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ status: 'accepted' }),
      { status: 202, headers: { 'content-type': 'application/json' } },
    ));
    vi.stubGlobal('fetch', fetchMock);
    const payload = {
      title: '지도 표시 오류',
      content: '강좌를 선택해도 지도에 위치가 표시되지 않습니다.',
      page_url: 'https://mooncen.test/?course=1',
      user_agent: 'test-browser',
      viewport: '1440x900',
      image_filename: null,
      image_media_type: null,
      image_base64: null,
    };

    await submitBugReport(payload);

    expect(fetchMock).toHaveBeenCalledOnce();
    const [path, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(options.headers);
    expect(path).toBe('/api/bug-reports');
    expect(options.method).toBe('POST');
    expect(options.credentials).toBe('include');
    expect(headers.get('Content-Type')).toBe('application/json');
    expect(headers.get('X-CSRF-Token')).toBe('csrf-test');
    expect(JSON.parse(String(options.body))).toEqual(payload);
  });
});
