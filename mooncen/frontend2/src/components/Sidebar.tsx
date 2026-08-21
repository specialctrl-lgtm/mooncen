import { type ReactNode, useEffect, useState } from 'react';
import {
  Baby,
  CalendarDays,
  ChevronDown,
  CircleDollarSign,
  Clock3,
  ClipboardCheck,
  MapPin,
  RotateCcw,
  SlidersHorizontal,
} from 'lucide-react';
import type { Branch } from '../api';
import { priorityCategoryLabelsForMode } from '../utils/categoryTaxonomy';
import ScopeIcon from './ScopeIcon';

type Option = {
  value: string;
  label: string;
  hint?: string;
};

type FilterIconName = 'provider' | 'branch' | 'category' | 'age' | 'date' | 'day' | 'time' | 'fee' | 'status';
type ScopeOption = {
  value: 'provider' | 'education' | 'experience';
  icon: 'culture' | 'education' | 'experience';
  title: string;
  description: string;
};

type SidebarProps = {
  mapMode: 'provider' | 'education' | 'experience';
  categoryOptions: Option[];
  ageOptions: Option[];
  providerOptions: Option[];
  categoryFilters: string[];
  ageFilters: string[] | null;
  branchFilterActive: boolean;
  childAgeMonths: string;
  childAgeYears: string;
  selectedDate: string;
  dayFilters: string[];
  timeFilters: string[];
  providerFilters: string[];
  feeFilters: string[];
  statusFilters: string[];
  selectedBranch: Branch | null;
  locationValue: string;
  locationPickerOpen: boolean;
  detailFilterCount: number;
  onClearSelectedBranch: () => void;
  onBranchSelectAll: (checked: boolean) => void;
  onOpenLocationPicker: () => void;
  onCategoryToggle: (value: string) => void;
  onCategorySelectAll: (checked: boolean) => void;
  onAgeToggle: (value: string) => void;
  onAgeSelectAll: (checked: boolean) => void;
  onChildAgeMonthsChange: (value: string) => void;
  onChildAgeYearsChange: (value: string) => void;
  onSelectedDateChange: (value: string) => void;
  onDayToggle: (value: string) => void;
  onDaySelectAll: (checked: boolean) => void;
  onTimeToggle: (value: string) => void;
  onTimeSelectAll: (checked: boolean) => void;
  onProviderToggle: (value: string) => void;
  onProviderSelectAll: (checked: boolean) => void;
  onFeeToggle: (value: string) => void;
  onFeeSelectAll: (checked: boolean) => void;
  onStatusToggle: (value: string) => void;
  onStatusSelectAll: (checked: boolean) => void;
  onMapModeChange: (value: 'provider' | 'education' | 'experience') => void;
  onResetFilters: () => void;
  closeSignal?: number;
};

const scopeOptions: ScopeOption[] = [
  {
    value: 'provider',
    icon: 'culture',
    title: '문화센터',
    description: '백화점·마트 문화센터',
  },
  {
    value: 'experience',
    icon: 'experience',
    title: '전시·체험',
    description: '도서관·과학관·미술관·박물관',
  },
  {
    value: 'education',
    icon: 'education',
    title: '평생교육',
    description: '시청·군청·구청·주민센터 강좌',
  },
];

const fees = [
  { value: 'free', label: '무료' },
  { value: 'under50000', label: '5만원 이하' },
  { value: 'under100000', label: '10만원 이하' },
  { value: 'over100000', label: '10만원 초과' },
];

const statuses = [
  { value: 'OPEN', label: '접수중' },
  { value: 'SCHEDULED', label: '접수예정' },
  { value: 'DEADLINE', label: '마감임박' },
  { value: 'WAITING', label: '대기접수' },
  { value: 'CLOSED', label: '마감' },
];

const days = [
  { value: '월', label: '월' },
  { value: '화', label: '화' },
  { value: '수', label: '수' },
  { value: '목', label: '목' },
  { value: '금', label: '금' },
  { value: '토', label: '토' },
  { value: '일', label: '일' },
  { value: '요일 미정', label: '요일 미정' },
];

const times = [
  { value: 'morning', label: '오전' },
  { value: 'afternoon', label: '오후' },
  { value: 'evening', label: '저녁' },
  { value: 'time_unknown', label: '시간 미정' },
];

function valuesCoverOptions(options: Option[], values: string[] | null) {
  if (values === null || values.length !== options.length) return false;
  const valueSet = new Set(values);
  return options.every((option) => valueSet.has(option.value));
}

function selectedLabel(options: Option[], values: string[] | null) {
  if (values === null) return '전체';
  if (!options.length) return '전체';
  if (valuesCoverOptions(options, values)) return '전체';
  if (!values.length) return '전체';
  if (values.length === 1) {
    return options.find((option) => option.value === values[0])?.label || values[0];
  }
  return `${values.length.toLocaleString('ko-KR')}개 선택`;
}

const calendarWeekdays = ['일', '월', '화', '수', '목', '금', '토'];

function startOfLocalMonth(date = new Date()) {
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

function parseLocalDate(value: string) {
  const [year, month, day] = value.split('-').map(Number);
  if (!year || !month || !day) return null;
  return new Date(year, month - 1, day);
}

function formatLocalDate(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function monthFromDateValue(value: string) {
  return startOfLocalMonth(parseLocalDate(value) || new Date());
}

function addCalendarMonths(date: Date, delta: number) {
  return new Date(date.getFullYear(), date.getMonth() + delta, 1);
}

function sameCalendarDate(left: Date, right: Date) {
  return formatLocalDate(left) === formatLocalDate(right);
}

function buildCalendarCells(month: Date) {
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

type MiniDateCalendarProps = {
  month: Date;
  selectedDate: string;
  onMonthChange: (date: Date) => void;
  onSelect: (value: string) => void;
  onClear: () => void;
};

function MiniDateCalendar({ month, selectedDate, onMonthChange, onSelect, onClear }: MiniDateCalendarProps) {
  const selected = parseLocalDate(selectedDate);
  const today = new Date();
  const cells = buildCalendarCells(month);
  const monthTitle = `${month.getFullYear()}.${String(month.getMonth() + 1).padStart(2, '0')}`;

  return (
    <div className="branch-quick-filter-menu branch-quick-calendar main-quick-calendar" role="dialog" aria-label="날짜 선택">
      <div className="branch-quick-calendar-header">
        <button className="branch-quick-calendar-nav" type="button" aria-label="이전 달" onClick={() => onMonthChange(addCalendarMonths(month, -1))}>
          &lt;
        </button>
        <strong>{monthTitle}</strong>
        <button className="branch-quick-calendar-nav" type="button" aria-label="다음 달" onClick={() => onMonthChange(addCalendarMonths(month, 1))}>
          &gt;
        </button>
      </div>
      <div className="branch-quick-calendar-weekdays" aria-hidden="true">
        {calendarWeekdays.map((weekday) => <span key={weekday}>{weekday}</span>)}
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

type QuickFilterMenuProps = {
  label: string;
  value: string;
  icon: ReactNode;
  open: boolean;
  options: Option[];
  selectedValues: string[];
  className?: string;
  children?: ReactNode;
  onToggleOpen: () => void;
  onToggle: (value: string) => void;
  onSelectAll: (checked: boolean) => void;
};

function QuickFilterMenu({
  label,
  value,
  icon,
  open,
  options,
  selectedValues,
  className,
  children,
  onToggleOpen,
  onToggle,
  onSelectAll,
}: QuickFilterMenuProps) {
  const allMode = selectedValues.length === 0 || valuesCoverOptions(options, selectedValues);
  return (
    <div className={['branch-quick-filter main-quick-filter', className].filter(Boolean).join(' ')}>
      <button
        className={`branch-quick-filter-chip main-quick-filter-chip ${open ? 'active' : ''}`}
        type="button"
        onClick={onToggleOpen}
        aria-expanded={open}
      >
        <span className="filter-control-icon" aria-hidden="true">{icon}</span>
        <span className="filter-control-copy">
          <strong>{label}</strong>
          <small>{value}</small>
        </span>
        <ChevronDown className="filter-control-chevron" size={16} strokeWidth={2} aria-hidden="true" />
      </button>
      {open && (
        <div className="branch-quick-filter-menu main-quick-filter-menu" role="group" aria-label={`${label} 선택`}>
          <label className="main-quick-filter-check">
            <input
              type="checkbox"
              checked={allMode}
              onChange={() => onSelectAll(true)}
            />
            <span>전체</span>
          </label>
          {options.map((option) => (
            <label className="main-quick-filter-check" key={option.value}>
              <input
                type="checkbox"
                checked={!allMode && selectedValues.includes(option.value)}
                onChange={() => onToggle(option.value)}
              />
              <span>
                {option.label}
                {option.hint && <small className="main-quick-filter-option-hint">{option.hint}</small>}
              </span>
            </label>
          ))}
          {children}
        </div>
      )}
    </div>
  );
}

export default function Sidebar({
  mapMode,
  categoryOptions,
  ageOptions,
  categoryFilters,
  ageFilters,
  branchFilterActive,
  childAgeMonths,
  childAgeYears,
  selectedDate,
  dayFilters,
  timeFilters,
  feeFilters,
  statusFilters,
  locationValue,
  locationPickerOpen,
  detailFilterCount,
  onBranchSelectAll,
  onOpenLocationPicker,
  onCategoryToggle,
  onCategorySelectAll,
  onAgeToggle,
  onAgeSelectAll,
  onChildAgeMonthsChange,
  onChildAgeYearsChange,
  onSelectedDateChange,
  onDayToggle,
  onDaySelectAll,
  onTimeToggle,
  onTimeSelectAll,
  onFeeToggle,
  onFeeSelectAll,
  onStatusToggle,
  onStatusSelectAll,
  onMapModeChange,
  onResetFilters,
  closeSignal,
}: SidebarProps) {
  const [openMenu, setOpenMenu] = useState<FilterIconName | null>(null);
  const [detailExpanded, setDetailExpanded] = useState(false);
  const [quickCalendarMonth, setQuickCalendarMonth] = useState(() => monthFromDateValue(selectedDate));

  useEffect(() => {
    setOpenMenu(null);
    setDetailExpanded(false);
  }, [closeSignal]);

  useEffect(() => {
    if (!detailExpanded) return undefined;
    const closeDetailPanel = (event: MouseEvent | TouchEvent) => {
      const target = event.target;
      if (target instanceof Element && target.closest('.mooncen-filter-detail')) return;
      setDetailExpanded(false);
      setOpenMenu(null);
    };
    document.addEventListener('mousedown', closeDetailPanel);
    document.addEventListener('touchstart', closeDetailPanel);
    return () => {
      document.removeEventListener('mousedown', closeDetailPanel);
      document.removeEventListener('touchstart', closeDetailPanel);
    };
  }, [detailExpanded]);

  useEffect(() => {
    if (!openMenu) return undefined;
    const closeMenu = (event: MouseEvent | TouchEvent) => {
      const target = event.target;
      if (target instanceof Element && target.closest('.main-quick-filter')) return;
      setOpenMenu(null);
    };
    document.addEventListener('mousedown', closeMenu);
    document.addEventListener('touchstart', closeMenu);
    return () => {
      document.removeEventListener('mousedown', closeMenu);
      document.removeEventListener('touchstart', closeMenu);
    };
  }, [openMenu]);
  const activeChips = [
    branchFilterActive
      ? {
          key: 'branch',
          label: `지점: ${locationValue}`,
          onRemove: () => onBranchSelectAll(true),
        }
      : null,
    !valuesCoverOptions(categoryOptions, categoryFilters)
      ? {
          key: 'category',
          label: `카테고리: ${selectedLabel(categoryOptions, categoryFilters)}`,
          onRemove: () => onCategorySelectAll(true),
        }
      : null,
    ageFilters !== null && !valuesCoverOptions(ageOptions, ageFilters)
      ? {
          key: 'age',
          label: `연령: ${selectedLabel(ageOptions, ageFilters)}`,
          onRemove: () => onAgeSelectAll(true),
        }
      : null,
    childAgeMonths
      ? {
          key: 'childAgeMonths',
          label: `${childAgeMonths}개월`,
          onRemove: () => onChildAgeMonthsChange(''),
        }
      : null,
    childAgeYears
      ? {
          key: 'childAgeYears',
          label: `만 ${childAgeYears}세`,
          onRemove: () => onChildAgeYearsChange(''),
        }
      : null,
    selectedDate
      ? {
          key: 'date',
          label: selectedDate,
          onRemove: () => onSelectedDateChange(''),
        }
      : null,
    dayFilters.length !== days.length
      ? {
          key: 'day',
          label: `요일: ${selectedLabel(days, dayFilters)}`,
          onRemove: () => onDaySelectAll(true),
        }
      : null,
    timeFilters.length !== times.length
      ? {
          key: 'time',
          label: `시간: ${selectedLabel(times, timeFilters)}`,
          onRemove: () => onTimeSelectAll(true),
        }
      : null,
    feeFilters.length !== fees.length
      ? {
          key: 'fee',
          label: `수강료: ${selectedLabel(fees, feeFilters)}`,
          onRemove: () => onFeeSelectAll(true),
        }
      : null,
    !valuesCoverOptions(statuses, statusFilters)
      ? {
          key: 'status',
          label: `접수: ${selectedLabel(statuses, statusFilters)}`,
          onRemove: () => onStatusSelectAll(true),
        }
      : null,
  ].filter((chip): chip is { key: string; label: string; onRemove: () => void } => Boolean(chip));
  const summaryLabels = activeChips.map((chip) => chip.label.replace(/^[^:]+:\s*/, ''));
  const filterSummary =
    summaryLabels.length === 0
      ? '전체 강좌 보기'
      : `${summaryLabels.slice(0, 3).join(' · ')}${summaryLabels.length > 3 ? ` 외 ${summaryLabels.length - 3}개` : ''}`;
  const sourceCategoryLabels = priorityCategoryLabelsForMode(mapMode);
  const displayCategoryOptions = sourceCategoryLabels.map((label) => {
    const exact = categoryOptions.find((option) => option.label === label || option.value === label);
    const matched = exact || { value: label, label };
    return { ...matched, displayLabel: label };
  });
  return (
    <aside className="sidebar mooncen-filter-sidebar" aria-label={`강좌 필터: ${filterSummary}`}>
      <div className="filter-panel mooncen-filter-bar">
        <section className="mooncen-filter-scope" aria-label="탐색 범위">
          <div className="filter-mode-selector" role="tablist" aria-label="탐색 범위">
            {scopeOptions.map((option) => {
              const active = mapMode === option.value;
              return (
                <button
                  key={option.value}
                  className={active ? 'active' : undefined}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  onClick={() => onMapModeChange(option.value)}
                >
                  <ScopeIcon type={option.icon} active={active} />
                  <span className="scope-card-copy">
                    <strong>{option.title}</strong>
                    <small>{option.description}</small>
                  </span>
                </button>
              );
            })}
          </div>
        </section>

        <section className="mooncen-filter-detail" aria-label="상세 조건">
          <div className="mooncen-filter-controls">
            <div className="branch-quick-filter main-quick-filter main-quick-filter-location">
              <button
                className={`branch-quick-filter-chip main-quick-filter-chip ${locationPickerOpen ? 'active' : ''}`}
                type="button"
                aria-haspopup="dialog"
                aria-expanded={locationPickerOpen}
                onClick={() => {
                  setOpenMenu(null);
                  setDetailExpanded(false);
                  onOpenLocationPicker();
                }}
              >
                <span className="filter-control-icon" aria-hidden="true">
                  <MapPin size={19} strokeWidth={2} />
                </span>
                <span className="filter-control-copy">
                  <strong>위치</strong>
                  <small>{locationValue}</small>
                </span>
              </button>
            </div>

            <QuickFilterMenu
              label="연령"
              value={selectedLabel(ageOptions, ageFilters)}
              icon={<Baby size={19} strokeWidth={2} />}
              className="main-quick-filter-age"
              open={openMenu === 'age'}
              options={ageOptions}
              selectedValues={ageFilters ?? []}
              onToggleOpen={() => setOpenMenu(openMenu === 'age' ? null : 'age')}
              onToggle={onAgeToggle}
              onSelectAll={onAgeSelectAll}
            >
              <label className="age-month-input in-filter-menu">
                <span>개월수</span>
                <input
                  type="number"
                  min="0"
                  max="240"
                  inputMode="numeric"
                  placeholder="예: 36"
                  value={childAgeMonths}
                  onChange={(event) => onChildAgeMonthsChange(event.currentTarget.value)}
                />
              </label>
              <label className="age-month-input">
                <span>만 나이</span>
                <input
                  type="number"
                  min="0"
                  max="120"
                  inputMode="numeric"
                  placeholder="예: 4"
                  value={childAgeYears}
                  onChange={(event) => onChildAgeYearsChange(event.currentTarget.value)}
                />
              </label>
            </QuickFilterMenu>

            <QuickFilterMenu
              label="요일"
              value={selectedLabel(days, dayFilters)}
              icon={<CalendarDays size={19} strokeWidth={2} />}
              open={openMenu === 'day'}
              options={days}
              selectedValues={dayFilters}
              onToggleOpen={() => setOpenMenu(openMenu === 'day' ? null : 'day')}
              onToggle={onDayToggle}
              onSelectAll={onDaySelectAll}
            />

            <QuickFilterMenu
              label="시간"
              value={selectedLabel(times, timeFilters)}
              icon={<Clock3 size={19} strokeWidth={2} />}
              open={openMenu === 'time'}
              options={times}
              selectedValues={timeFilters}
              onToggleOpen={() => setOpenMenu(openMenu === 'time' ? null : 'time')}
              onToggle={onTimeToggle}
              onSelectAll={onTimeSelectAll}
            />
          </div>

          <div className="mooncen-filter-actions">
            <button
              className={`filter-detail-button ${detailExpanded ? 'active' : ''}`}
              type="button"
              aria-expanded={detailExpanded}
              onClick={() => {
                setDetailExpanded((value) => !value);
                setOpenMenu(null);
              }}
            >
              <SlidersHorizontal size={18} strokeWidth={2} aria-hidden="true" />
              <span>상세필터</span>
              {detailFilterCount > 0 && (
                <b className="detail-filter-count" aria-label={`${detailFilterCount}개 조건 적용`}>
                  {detailFilterCount}
                </b>
              )}
            </button>
          </div>

          {detailExpanded && (
            <div className="mooncen-filter-extra-panel" role="group" aria-label="상세필터 추가 조건">
              <div className="top-category-filter-group" aria-label="분야">
                <span className="mooncen-filter-title">분야</span>
                <div className="dashboard-category-chips" aria-label="분야">
                  <button
                    className={categoryFilters.length === categoryOptions.length ? 'active' : undefined}
                    type="button"
                    onClick={() => onCategorySelectAll(true)}
                  >
                    전체
                  </button>
                  {displayCategoryOptions.map((option) => (
                    <button
                      key={`${mapMode}:${option.displayLabel}`}
                      className={categoryFilters.includes(option.value) && categoryFilters.length !== categoryOptions.length ? 'active' : undefined}
                      type="button"
                      onClick={() => onCategoryToggle(option.value)}
                    >
                      {option.displayLabel}
                    </button>
                  ))}
                </div>
              </div>
              <div className="top-detail-filter-row">
                <div className="top-detail-filter-heading">
                  <span className="mooncen-filter-title">상세 조건</span>
                  <button
                    className="filter-reset-button"
                    type="button"
                    aria-label="상세 필터 초기화"
                    title="필터 초기화"
                    onClick={onResetFilters}
                  >
                    <RotateCcw size={16} strokeWidth={2} aria-hidden="true" />
                  </button>
                </div>
                <div className="top-detail-filter-controls">
                  <div className="branch-quick-filter main-quick-filter">
                    <button
                      className={`branch-quick-filter-chip main-quick-filter-chip ${openMenu === 'date' ? 'active' : ''}`}
                      type="button"
                      onClick={() => {
                        const willOpen = openMenu !== 'date';
                        if (willOpen) setQuickCalendarMonth(monthFromDateValue(selectedDate));
                        setOpenMenu(willOpen ? 'date' : null);
                      }}
                      aria-expanded={openMenu === 'date'}
                    >
                      <span className="filter-control-icon" aria-hidden="true">
                        <CalendarDays size={19} strokeWidth={2} />
                      </span>
                      <span className="filter-control-copy">
                        <strong>날짜</strong>
                        <small>{selectedDate || '전체'}</small>
                      </span>
                      <ChevronDown className="filter-control-chevron" size={16} strokeWidth={2} aria-hidden="true" />
                    </button>
                    {openMenu === 'date' && (
                      <MiniDateCalendar
                        month={quickCalendarMonth}
                        selectedDate={selectedDate}
                        onMonthChange={setQuickCalendarMonth}
                        onSelect={(value) => {
                          onSelectedDateChange(value);
                          setOpenMenu(null);
                        }}
                        onClear={() => {
                          onSelectedDateChange('');
                          setOpenMenu(null);
                        }}
                      />
                    )}
                  </div>
                  <QuickFilterMenu
                    label="모집상태"
                    value={selectedLabel(statuses, statusFilters)}
                    icon={<ClipboardCheck size={19} strokeWidth={2} />}
                    open={openMenu === 'status'}
                    options={statuses}
                    selectedValues={statusFilters}
                    onToggleOpen={() => setOpenMenu(openMenu === 'status' ? null : 'status')}
                    onToggle={onStatusToggle}
                    onSelectAll={onStatusSelectAll}
                  />
                  <QuickFilterMenu
                    label="수강료"
                    value={selectedLabel(fees, feeFilters)}
                    icon={<CircleDollarSign size={19} strokeWidth={2} />}
                    open={openMenu === 'fee'}
                    options={fees}
                    selectedValues={feeFilters}
                    onToggleOpen={() => setOpenMenu(openMenu === 'fee' ? null : 'fee')}
                    onToggle={onFeeToggle}
                    onSelectAll={onFeeSelectAll}
                  />
                </div>
              </div>
            </div>
          )}
        </section>

      </div>
    </aside>
  );
}
