import {
  addCalendarMonths,
  buildCalendarCells,
  formatLocalDate,
  parseLocalDate,
  sameCalendarDate,
} from '../utils/calendar';

type QuickDateCalendarProps = {
  month: Date;
  selectedDate: string;
  onMonthChange: (date: Date) => void;
  onSelect: (value: string) => void;
  onClear: () => void;
};

const calendarWeekdays = ['일', '월', '화', '수', '목', '금', '토'];

export default function QuickDateCalendar({
  month,
  selectedDate,
  onMonthChange,
  onSelect,
  onClear,
}: QuickDateCalendarProps) {
  const selected = parseLocalDate(selectedDate);
  const today = new Date();
  const cells = buildCalendarCells(month);
  const monthTitle = `${month.getFullYear()}.${String(month.getMonth() + 1).padStart(2, '0')}`;

  return (
    <div className="branch-quick-filter-menu branch-quick-calendar" role="dialog" aria-label="날짜 선택">
      <div className="branch-quick-calendar-header">
        <button
          className="branch-quick-calendar-nav"
          type="button"
          aria-label="이전 달"
          onClick={() => onMonthChange(addCalendarMonths(month, -1))}
        >
          &lt;
        </button>
        <strong>{monthTitle}</strong>
        <button
          className="branch-quick-calendar-nav"
          type="button"
          aria-label="다음 달"
          onClick={() => onMonthChange(addCalendarMonths(month, 1))}
        >
          &gt;
        </button>
      </div>
      <div className="branch-quick-calendar-weekdays" aria-hidden="true">
        {calendarWeekdays.map((weekday) => (
          <span key={weekday}>{weekday}</span>
        ))}
      </div>
      <div className="branch-quick-calendar-grid">
        {cells.map((cell) => {
          const isSelected = selected ? sameCalendarDate(cell.date, selected) : false;
          const isToday = sameCalendarDate(cell.date, today);
          return (
            <button
              key={cell.value}
              className={[
                'branch-quick-calendar-day',
                cell.inMonth ? '' : 'outside',
                isToday ? 'today' : '',
                isSelected ? 'selected' : '',
              ].filter(Boolean).join(' ')}
              type="button"
              aria-pressed={isSelected}
              onClick={() => onSelect(cell.value)}
            >
              {cell.date.getDate()}
            </button>
          );
        })}
      </div>
      <div className="branch-quick-calendar-footer">
        <button type="button" onClick={() => onSelect(formatLocalDate(today))}>오늘</button>
        <button type="button" onClick={onClear}>전체</button>
      </div>
    </div>
  );
}
