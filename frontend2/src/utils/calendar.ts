export function startOfLocalMonth(date = new Date()) {
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

export function parseLocalDate(value: string) {
  const [year, month, day] = value.split('-').map(Number);
  if (!year || !month || !day) return null;
  return new Date(year, month - 1, day);
}

export function formatLocalDate(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

export function monthFromDateValue(value: string) {
  return startOfLocalMonth(parseLocalDate(value) || new Date());
}

export function addCalendarMonths(date: Date, delta: number) {
  return new Date(date.getFullYear(), date.getMonth() + delta, 1);
}

export function sameCalendarDate(left: Date, right: Date) {
  return formatLocalDate(left) === formatLocalDate(right);
}

export function buildCalendarCells(month: Date) {
  const first = startOfLocalMonth(month);
  const start = new Date(first.getFullYear(), first.getMonth(), 1 - first.getDay());
  return Array.from({ length: 42 }, (_, index) => {
    const date = new Date(start.getFullYear(), start.getMonth(), start.getDate() + index);
    return {
      date,
      value: formatLocalDate(date),
      inMonth: date.getMonth() === first.getMonth(),
    };
  });
}
