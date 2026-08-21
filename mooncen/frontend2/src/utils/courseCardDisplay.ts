import type { ClassItem } from '../data/mockData';

type DateParts = {
  year: number;
  month: number;
  day: number;
  weekday: string;
};

const weekdays = ['일', '월', '화', '수', '목', '금', '토'];

function parseDateParts(value?: string | null): DateParts | null {
  const match = value?.trim().match(/(\d{4})[-./](\d{1,2})[-./](\d{1,2})/);
  if (!match) return null;

  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const date = new Date(year, month - 1, day);
  if (
    Number.isNaN(date.getTime()) ||
    date.getFullYear() !== year ||
    date.getMonth() !== month - 1 ||
    date.getDate() !== day
  ) {
    return null;
  }
  return { year, month, day, weekday: weekdays[date.getDay()] };
}

function formatDateParts(parts: DateParts, includeWeekday: boolean) {
  const yearPrefix = parts.year === new Date().getFullYear() ? '' : `${parts.year}.`;
  return `${yearPrefix}${parts.month}.${parts.day}${includeWeekday ? `(${parts.weekday})` : ''}`;
}

export function hasUsefulCourseText(value?: string | null) {
  if (!value) return false;
  return !/(미정|확인 필요|없음|null|undefined)/i.test(value.trim());
}

function formatDays(days: string[]) {
  const normalized = [...new Set(days)]
    .map((day) => day.trim())
    .filter((day) => day && !/미정/.test(day))
    .map((day) => day.endsWith('요일') ? day : `${day}요일`);
  return normalized.length ? `매주 ${normalized.join('·')}` : '';
}

export function formatCourseSchedule(item: ClassItem) {
  const scheduleDates = item.scheduleDates
    .map(parseDateParts)
    .filter((date): date is DateParts => Boolean(date));
  const start = parseDateParts(item.startDate) ?? scheduleDates[0] ?? null;
  const end = parseDateParts(item.endDate) ?? scheduleDates[scheduleDates.length - 1] ?? null;
  const isRange = Boolean(
    start &&
    end &&
    (start.year !== end.year || start.month !== end.month || start.day !== end.day),
  );

  let dateText = '';
  if (start && end && isRange) {
    dateText = `${formatDateParts(start, false)}~${formatDateParts(end, false)}`;
  } else if (start) {
    dateText = formatDateParts(start, true);
  } else if (hasUsefulCourseText(item.scheduleDate)) {
    dateText = item.scheduleDate.trim();
  }

  const recurringDays = isRange || !dateText ? formatDays(item.scheduleDays) : '';
  const time = hasUsefulCourseText(item.scheduleTime)
    ? item.scheduleTime.trim()
    : item.schedule.match(/\d{1,2}:\d{2}\s*[~-]\s*\d{1,2}:\d{2}/)?.[0]?.replace(/\s+/g, '') || '';
  const main = [dateText, recurringDays, time].filter(Boolean).join(' ');
  const sessions = item.sessionLabel || (item.sessions && item.sessions > 0 ? `총 ${item.sessions}회` : '');
  return [main, sessions].filter(Boolean).join(' · ') || '일정은 상세 페이지에서 확인';
}

export function formatApplicationPeriod(item: ClassItem) {
  const start = parseDateParts(item.applyStart);
  const end = parseDateParts(item.applyEnd);
  if (start && end) {
    const sameDate = start.year === end.year && start.month === end.month && start.day === end.day;
    return sameDate
      ? formatDateParts(start, true)
      : `${formatDateParts(start, true)} ~ ${formatDateParts(end, true)}`;
  }
  if (start) return formatDateParts(start, true);
  if (end) return `~ ${formatDateParts(end, true)}`;
  if (hasUsefulCourseText(item.applyPeriodRaw)) return item.applyPeriodRaw?.trim() || '';
  return '접수 일정은 상세 페이지에서 확인';
}

export function formatCoursePrice(item: ClassItem) {
  const priceKnown = item.priceKnown || item.price > 0;
  if (!priceKnown) return '수강료 확인';
  if (item.price <= 0) return '무료';
  return `${item.price.toLocaleString('ko-KR')}원`;
}
