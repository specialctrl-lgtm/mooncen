import { describe, expect, it } from 'vitest';
import {
  addCalendarMonths,
  buildCalendarCells,
  formatLocalDate,
  monthFromDateValue,
  parseLocalDate,
} from './calendar';

describe('local calendar helpers', () => {
  it('round-trips a local date without UTC conversion', () => {
    const parsed = parseLocalDate('2026-07-10');

    expect(parsed).not.toBeNull();
    expect(formatLocalDate(parsed!)).toBe('2026-07-10');
    expect(monthFromDateValue('2026-07-10')).toEqual(new Date(2026, 6, 1));
  });

  it('builds a stable six-week calendar grid', () => {
    const cells = buildCalendarCells(new Date(2026, 6, 1));

    expect(cells).toHaveLength(42);
    expect(cells[0]).toMatchObject({ value: '2026-06-28', inMonth: false });
    expect(cells[3]).toMatchObject({ value: '2026-07-01', inMonth: true });
    expect(cells[cells.length - 1]).toMatchObject({ value: '2026-08-08', inMonth: false });
  });

  it('moves across year boundaries by calendar month', () => {
    expect(addCalendarMonths(new Date(2026, 11, 1), 1)).toEqual(new Date(2027, 0, 1));
  });
});
