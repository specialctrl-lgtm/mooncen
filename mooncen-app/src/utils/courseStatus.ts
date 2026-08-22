export type KnownCourseStatus =
  | "SCHEDULED"
  | "OPEN"
  | "WAITING"
  | "DEADLINE"
  | "CLOSED";

export type EffectiveCourseStatus = KnownCourseStatus | "UNKNOWN";

export type CourseApplicationStateInput = {
  status?: string | null;
  apply_end?: string | null;
  reservation_available?: boolean | null;
  application_url?: string | null;
  raw_url?: string | null;
};

export type CourseApplicationState = {
  status: EffectiveCourseStatus;
  applicationUrl: string | null;
  applicationExpired: boolean;
  explicitlyUnavailable: boolean;
  canApply: boolean;
};

const DATE_BOUND_APPLICATION_STATUSES = new Set<EffectiveCourseStatus>(["OPEN", "DEADLINE"]);
const ACTIONABLE_APPLICATION_STATUSES = new Set<EffectiveCourseStatus>([
  "OPEN",
  "DEADLINE",
  "WAITING",
]);
const KNOWN_STATUSES = new Set<EffectiveCourseStatus>([
  "SCHEDULED",
  "OPEN",
  "WAITING",
  "DEADLINE",
  "CLOSED",
]);

function validDateKey(year: number, month: number, day: number): string | null {
  const candidate = new Date(Date.UTC(year, month - 1, day));
  if (
    candidate.getUTCFullYear() !== year ||
    candidate.getUTCMonth() !== month - 1 ||
    candidate.getUTCDate() !== day
  ) {
    return null;
  }

  return `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

function dateKeyFromValue(value: string | null | undefined): string | null {
  const trimmed = value?.trim();
  if (!trimmed) return null;

  // The API models apply_end as a calendar date. Preserve that date instead of
  // allowing a timestamp offset to move it into another KST calendar day.
  const calendarMatch = trimmed.match(/^(\d{4})[-./](\d{1,2})[-./](\d{1,2})(?:$|[T\s])/);
  if (calendarMatch) {
    return validDateKey(
      Number(calendarMatch[1]),
      Number(calendarMatch[2]),
      Number(calendarMatch[3]),
    );
  }

  const parsed = new Date(trimmed);
  if (Number.isNaN(parsed.getTime())) return null;
  return getKstDateKey(parsed);
}

function safeHttpUrl(value: string | null | undefined): string | null {
  const trimmed = value?.trim();
  if (!trimmed) return null;

  try {
    const url = new URL(trimmed);
    if (!new Set(["http:", "https:"]).has(url.protocol)) return null;
    if (url.username || url.password) return null;
    return url.toString();
  } catch {
    return null;
  }
}

export function getKstDateKey(referenceDate = new Date()): string {
  const safeReference = Number.isNaN(referenceDate.getTime()) ? new Date() : referenceDate;
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(safeReference);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

export function normalizeCourseStatus(status: string | null | undefined): EffectiveCourseStatus {
  const normalized = status?.trim().toUpperCase() as EffectiveCourseStatus | undefined;
  return normalized && KNOWN_STATUSES.has(normalized) ? normalized : "UNKNOWN";
}

export function isCourseApplicationExpired(
  applyEnd: string | null | undefined,
  referenceDate = new Date(),
): boolean {
  const applyEndKey = dateKeyFromValue(applyEnd);
  return applyEndKey !== null && applyEndKey < getKstDateKey(referenceDate);
}

export function resolveCourseApplicationUrl(
  course: Pick<CourseApplicationStateInput, "application_url">,
): string | null {
  // raw_url is the source page and may only contain information. Treating it as
  // an application URL creates a misleading enabled CTA.
  return safeHttpUrl(course.application_url);
}

export function getEffectiveCourseStatus(
  course: Pick<
    CourseApplicationStateInput,
    "status" | "apply_end" | "reservation_available"
  >,
  referenceDate = new Date(),
): EffectiveCourseStatus {
  const status = normalizeCourseStatus(course.status);
  const shouldClose =
    DATE_BOUND_APPLICATION_STATUSES.has(status) &&
    (isCourseApplicationExpired(course.apply_end, referenceDate) ||
      course.reservation_available === false);
  return shouldClose ? "CLOSED" : status;
}

export function getCourseApplicationState(
  course: CourseApplicationStateInput,
  referenceDate = new Date(),
): CourseApplicationState {
  const applicationExpired = isCourseApplicationExpired(course.apply_end, referenceDate);
  const explicitlyUnavailable = course.reservation_available === false;
  const status = getEffectiveCourseStatus(course, referenceDate);
  const applicationUrl = resolveCourseApplicationUrl(course);

  return {
    status,
    applicationUrl,
    applicationExpired,
    explicitlyUnavailable,
    canApply:
      ACTIONABLE_APPLICATION_STATUSES.has(status) &&
      !explicitlyUnavailable &&
      applicationUrl !== null,
  };
}

export function canApplyToCourse(
  course: CourseApplicationStateInput,
  referenceDate = new Date(),
): boolean {
  return getCourseApplicationState(course, referenceDate).canApply;
}
