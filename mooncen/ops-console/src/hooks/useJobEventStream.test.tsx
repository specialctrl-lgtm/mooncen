import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { appendStreamLog, useJobEventStream } from './useJobEventStream';

class FakeEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static latest: FakeEventSource | null = null;

  readonly url: string;
  readyState = FakeEventSource.CONNECTING;
  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  private listeners = new Map<string, Array<(event: Event) => void>>();

  constructor(url: string | URL) {
    this.url = String(url);
    FakeEventSource.latest = this;
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject | null) {
    if (!listener) return;
    const callback = typeof listener === 'function' ? listener : (event: Event) => listener.handleEvent(event);
    this.listeners.set(type, [...(this.listeners.get(type) || []), callback]);
  }

  close() {
    this.readyState = FakeEventSource.CLOSED;
  }

  open() {
    this.readyState = FakeEventSource.OPEN;
    this.onopen?.(new Event('open'));
  }

  disconnect() {
    this.readyState = FakeEventSource.CONNECTING;
    this.onerror?.(new Event('error'));
  }

  emit(type: string, payload: Record<string, unknown>) {
    const event = new MessageEvent(type, { data: JSON.stringify(payload) });
    for (const listener of this.listeners.get(type) || []) listener(event);
  }
}

describe('useJobEventStream', () => {
  beforeEach(() => {
    FakeEventSource.latest = null;
    vi.stubGlobal('EventSource', FakeEventSource as unknown as typeof EventSource);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('updates from SSE payloads and reports reconnect without API invalidation polling', () => {
    const onJob = vi.fn();
    const onLog = vi.fn();
    const onEnd = vi.fn();
    const { result } = renderHook(() => useJobEventStream({
      jobId: 'job-id',
      enabled: true,
      onJob,
      onLog,
      onEnd,
    }));
    const stream = FakeEventSource.latest!;

    act(() => stream.open());
    expect(result.current.state).toBe('connected');

    act(() => {
      stream.emit('job', { id: 'job-id', status: 'running', progress: 42 });
      stream.emit('log', { id: 7, message: 'working' });
    });
    expect(onJob).toHaveBeenCalledWith(expect.objectContaining({ progress: 42 }));
    expect(onLog).toHaveBeenCalledWith(expect.objectContaining({ id: 7 }));

    act(() => stream.disconnect());
    expect(result.current.state).toBe('reconnecting');

    act(() => stream.emit('error', { detail: 'temporary database error', request_id: 'stream-request' }));
    expect(result.current.detail).toContain('stream-request');

    act(() => stream.emit('end', { status: 'success' }));
    expect(result.current.state).toBe('ended');
    expect(stream.readyState).toBe(FakeEventSource.CLOSED);
    expect(onEnd).toHaveBeenCalledWith({ status: 'success' });
  });

  it('deduplicates and bounds streamed log cache entries', () => {
    const current = { available: true, items: [{ id: 1, message: 'first' }] };

    expect(appendStreamLog(current, { id: 1, message: 'duplicate' })).toBe(current);
    expect(appendStreamLog(current, { id: 2, message: 'second' })?.items).toEqual([
      { id: 1, message: 'first' },
      { id: 2, message: 'second' },
    ]);
  });
});
