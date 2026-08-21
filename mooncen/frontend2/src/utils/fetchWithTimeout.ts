export class RequestTimeoutError extends Error {
  constructor() {
    super('요청 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.');
    this.name = 'RequestTimeoutError';
  }
}

export async function fetchWithTimeout(
  input: RequestInfo | URL,
  init: RequestInit = {},
  timeoutMs = 12_000,
) {
  const controller = new AbortController();
  let timedOut = false;
  const relayAbort = () => controller.abort(init.signal?.reason);

  if (init.signal?.aborted) relayAbort();
  else init.signal?.addEventListener('abort', relayAbort, { once: true });

  const timer = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  try {
    return await fetch(input, { ...init, signal: controller.signal });
  } catch (error) {
    if (timedOut) throw new RequestTimeoutError();
    throw error;
  } finally {
    window.clearTimeout(timer);
    init.signal?.removeEventListener('abort', relayAbort);
  }
}
