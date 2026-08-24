export const statusLabels: Record<string, string> = {
  healthy: '정상',
  warning: '주의',
  critical: '장애',
  unknown: '확인 불가',
  disabled: '비활성',
  idle: '대기',
  queued: '대기열',
  assigned: '할당',
  running: '실행 중',
  success: '성공',
  passed: 'PASS',
  ready: '준비됨',
  partial_success: '일부 성공',
  failed: '실패',
  stopping: '중지 중',
  cancelled: '취소',
  timed_out: '시간 초과',
  blocked: '차단',
  open: '열림',
  reviewing: '검토 중',
  resolved: '해결',
  ignored: '무시',
  auto_fixed: '자동 수정',
};

export function formatDate(value: unknown): string {
  if (!value) return '-';
  const parsed = new Date(String(value));
  if (Number.isNaN(parsed.getTime())) return String(value);
  return new Intl.DateTimeFormat('ko-KR', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(parsed);
}

export function formatNumber(value: unknown): string {
  const number = Number(value || 0);
  return Number.isFinite(number) ? number.toLocaleString('ko-KR') : '-';
}

export function csvCellText(value: unknown): string {
  const text = typeof value === 'object' && value !== null ? JSON.stringify(value) : String(value ?? '');
  return typeof value === 'string' && /^[\t\r\n ]*[=+\-@]/.test(text)
    ? `'${text}`
    : text;
}

export function downloadCsv(filename: string, rows: Array<Record<string, unknown>>): void {
  if (!rows.length) return;
  const columns = Array.from(new Set(rows.flatMap((row) => Object.keys(row))));
  const quote = (value: unknown) => {
    return `"${csvCellText(value).replace(/"/g, '""')}"`;
  };
  const csv = [columns.map(quote).join(','), ...rows.map((row) => columns.map((column) => quote(row[column])).join(','))].join(
    '\r\n',
  );
  const blob = new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
