const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');
const CSRF_COOKIE_NAME = import.meta.env.VITE_OPS_CSRF_COOKIE_NAME || 'mooncen_ops_csrf';
export const OPS_REQUEST_TIMEOUT_MS = 12_000;

export type OpsApiErrorKind = 'http' | 'timeout' | 'network' | 'cancelled' | 'invalid-response';

export class OpsApiError extends Error {
  status: number;
  requestId: string | null;
  kind: OpsApiErrorKind;

  constructor(
    status: number,
    message: string,
    options: { requestId?: string | null; kind?: OpsApiErrorKind; cause?: unknown } = {},
  ) {
    super(message);
    this.name = 'OpsApiError';
    this.status = status;
    this.requestId = options.requestId || null;
    this.kind = options.kind || 'http';
    if (options.cause !== undefined) {
      Object.defineProperty(this, 'cause', { value: options.cause, configurable: true });
    }
  }
}

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

function csrfToken(): string {
  const prefix = `${CSRF_COOKIE_NAME}=`;
  const cookie = document.cookie
    .split(';')
    .map((item) => item.trim())
    .find((item) => item.startsWith(prefix));
  return cookie ? decodeURIComponent(cookie.slice(prefix.length)) : '';
}

export function isOpsUnauthorized(error: unknown): error is OpsApiError {
  return error instanceof OpsApiError && error.status === 401;
}

export async function opsFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
  timeoutMs = OPS_REQUEST_TIMEOUT_MS,
): Promise<Response> {
  const controller = new AbortController();
  const sourceSignal = init.signal;
  let timedOut = false;
  const relayAbort = () => controller.abort(sourceSignal?.reason);
  if (sourceSignal?.aborted) relayAbort();
  else sourceSignal?.addEventListener('abort', relayAbort, { once: true });
  const timer = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  try {
    return await fetch(input, { ...init, signal: controller.signal });
  } catch (error: unknown) {
    if (timedOut) {
      throw new OpsApiError(0, '요청 시간이 초과되었습니다. Ops API 상태를 확인하고 다시 시도해 주세요.', {
        kind: 'timeout',
        cause: error,
      });
    }
    if (sourceSignal?.aborted) {
      throw new OpsApiError(0, '요청이 취소되었습니다.', { kind: 'cancelled', cause: error });
    }
    throw new OpsApiError(0, 'Ops API에 연결하지 못했습니다. API와 SSH 터널 상태를 확인해 주세요.', {
      kind: 'network',
      cause: error,
    });
  } finally {
    window.clearTimeout(timer);
    sourceSignal?.removeEventListener('abort', relayAbort);
  }
}

export async function opsErrorFromResponse(response: Response, fallback?: string): Promise<OpsApiError> {
  let message = fallback || `${response.status} ${response.statusText}`.trim();
  let payloadRequestId: string | null = null;
  try {
    const payload = await response.json();
    if (typeof payload?.detail === 'string') message = payload.detail;
    else if (typeof payload?.detail?.message === 'string') message = payload.detail.message;
    if (typeof payload?.request_id === 'string') payloadRequestId = payload.request_id.slice(0, 128);
  } catch {
    // The bounded fallback and response header remain available.
  }
  const requestId = payloadRequestId || response.headers.get('X-Request-ID')?.slice(0, 128) || null;
  return new OpsApiError(response.status, message, { requestId, kind: 'http' });
}

export async function opsApi<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method || 'GET').toUpperCase();
  const headers = new Headers(init.headers);
  headers.set('Accept', 'application/json');
  if (init.body) headers.set('Content-Type', 'application/json');
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const token = csrfToken();
    if (token) headers.set('X-CSRF-Token', token);
  }
  const response = await opsFetch(apiUrl(`/api/ops${path}`), {
    ...init,
    headers,
    credentials: 'include',
  });
  if (!response.ok) {
    const error = await opsErrorFromResponse(response);
    if (error.status === 401 && path !== '/session') {
      window.dispatchEvent(new Event('mooncen:ops-auth-expired'));
    }
    throw error;
  }
  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get('content-type') || '';
  if (!contentType.includes('application/json')) {
    throw new OpsApiError(response.status, 'Ops API가 JSON이 아닌 응답을 반환했습니다. 프록시 설정을 확인해 주세요.', {
      requestId: response.headers.get('X-Request-ID'),
      kind: 'invalid-response',
    });
  }
  try {
    return await response.json() as T;
  } catch (error: unknown) {
    throw new OpsApiError(response.status, 'Ops API JSON 응답을 해석하지 못했습니다.', {
      requestId: response.headers.get('X-Request-ID'),
      kind: 'invalid-response',
      cause: error,
    });
  }
}

export function opsStreamUrl(path: string): string {
  return `${API_BASE}/api/ops${path}`;
}
