import { useEffect, useRef, useState } from 'react';
import { opsStreamUrl } from '../api';
import type { StreamConnectionState } from '../components/Ui';

type StreamRecord = Record<string, unknown>;

type JobEventStreamOptions = {
  jobId: string;
  enabled: boolean;
  onJob: (job: StreamRecord) => void;
  onLog: (log: StreamRecord) => void;
  onEnd: (payload: StreamRecord) => void;
};

function eventRecord(event: Event): StreamRecord | null {
  if (!(event instanceof MessageEvent) || typeof event.data !== 'string') return null;
  try {
    const value = JSON.parse(event.data) as unknown;
    return value && typeof value === 'object' && !Array.isArray(value) ? value as StreamRecord : null;
  } catch {
    return null;
  }
}

export function useJobEventStream({ jobId, enabled, onJob, onLog, onEnd }: JobEventStreamOptions) {
  const callbacks = useRef({ onJob, onLog, onEnd });
  callbacks.current = { onJob, onLog, onEnd };
  const [state, setState] = useState<StreamConnectionState>('idle');
  const [detail, setDetail] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled || !jobId) {
      setState('idle');
      setDetail(null);
      return undefined;
    }
    if (typeof EventSource === 'undefined') {
      setState('reconnecting');
      setDetail('이 브라우저는 실시간 상태 연결을 지원하지 않습니다. 새로고침으로 상태를 확인하세요.');
      return undefined;
    }

    setState('connecting');
    setDetail(null);
    const stream = new EventSource(opsStreamUrl(`/jobs/${jobId}/stream`), { withCredentials: true });
    stream.onopen = () => {
      setState('connected');
      setDetail(null);
    };
    stream.onerror = () => {
      if (stream.readyState !== EventSource.CLOSED) setState('reconnecting');
    };
    stream.addEventListener('job', (event) => {
      const record = eventRecord(event);
      if (record) callbacks.current.onJob(record);
    });
    stream.addEventListener('log', (event) => {
      const record = eventRecord(event);
      if (record) callbacks.current.onLog(record);
    });
    stream.addEventListener('error', (event) => {
      const record = eventRecord(event);
      if (!record) return;
      const message = typeof record.detail === 'string' ? record.detail : '실시간 연결 오류';
      const requestId = typeof record.request_id === 'string' ? ` 요청 ID: ${record.request_id}` : '';
      setDetail(`${message}${requestId}`);
      setState('reconnecting');
    });
    stream.addEventListener('end', (event) => {
      const record = eventRecord(event) || {};
      stream.close();
      setState('ended');
      setDetail(null);
      callbacks.current.onEnd(record);
    });
    return () => stream.close();
  }, [enabled, jobId]);

  return { state, detail };
}

export function appendStreamLog(
  current: { available: boolean; items: Array<Record<string, unknown>> } | undefined,
  log: Record<string, unknown>,
) {
  const items = current?.items || [];
  if (items.some((item) => String(item.id) === String(log.id))) return current;
  return { available: current?.available !== false, items: [...items, log].slice(-1_000) };
}
