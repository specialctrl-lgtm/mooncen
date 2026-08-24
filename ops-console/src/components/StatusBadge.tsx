import { statusLabels } from '../utils';

export default function StatusBadge({ status }: { status: string | null | undefined }) {
  const value = status || 'unknown';
  return <span className={`status-badge status-${value}`}>{statusLabels[value] || value}</span>;
}
