import type { ReactNode } from 'react';
import { OpsApiError } from '../api';

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        {eyebrow && <span className="eyebrow">{eyebrow}</span>}
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

export function StatCard({
  label,
  value,
  note,
  tone = 'neutral',
}: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
  tone?: 'neutral' | 'good' | 'warn' | 'bad';
}) {
  return (
    <article className={`stat-card tone-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      {note && <small>{note}</small>}
    </article>
  );
}

export function QueryState({
  loading,
  error,
  unavailable,
  empty,
  action,
}: {
  loading?: boolean;
  error?: unknown;
  unavailable?: boolean;
  empty?: boolean;
  action?: ReactNode;
}) {
  if (loading) return <div className="state-panel">실제 운영 데이터를 불러오는 중입니다.</div>;
  if (error) {
    const requestId = error instanceof OpsApiError ? error.requestId : null;
    return (
      <div className="state-panel state-error" role="alert">
        <span>조회 실패: {error instanceof Error ? error.message : '알 수 없는 오류'}</span>
        {requestId && <small>요청 ID: <code>{requestId}</code></small>}
        {action}
      </div>
    );
  }
  if (unavailable) return <div className="state-panel">연동되지 않았습니다. 필요한 DB 마이그레이션 또는 Agent 상태를 확인하세요.</div>;
  if (empty) return <div className="state-panel">조건에 해당하는 실제 데이터가 없습니다.</div>;
  return null;
}

export type StreamConnectionState = 'idle' | 'connecting' | 'connected' | 'reconnecting' | 'ended';

export function StreamStatus({
  state,
  detail,
}: {
  state: StreamConnectionState;
  detail?: string | null;
}) {
  if (state === 'idle' || state === 'ended') return null;
  const label = state === 'connected'
    ? '실시간 연결됨'
    : state === 'reconnecting'
      ? '실시간 연결이 끊겨 재연결 중입니다.'
      : '실시간 연결 중입니다.';
  return (
    <p className={`stream-status stream-${state}`} role="status" aria-live="polite">
      {label}{detail ? ` ${detail}` : ''}
    </p>
  );
}

export function DetailPanel({
  title,
  children,
  onClose,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
}) {
  return (
    <div className="detail-scrim" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <aside className="detail-panel" role="dialog" aria-modal="true" aria-label={title}>
        <header>
          <h2>{title}</h2>
          <button className="icon-button" type="button" onClick={onClose} aria-label="상세 닫기">
            ×
          </button>
        </header>
        <div className="detail-body">{children}</div>
      </aside>
    </div>
  );
}

export function DefinitionList({ value }: { value: Record<string, unknown> }) {
  return (
    <dl className="definition-list">
      {Object.entries(value).map(([key, item]) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd>{typeof item === 'object' && item !== null ? <pre>{JSON.stringify(item, null, 2)}</pre> : String(item ?? '-')}</dd>
        </div>
      ))}
    </dl>
  );
}
