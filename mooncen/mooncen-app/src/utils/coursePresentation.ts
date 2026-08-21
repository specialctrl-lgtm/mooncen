import dayjs from "dayjs";

import type { CourseDto, CourseScope } from "../api/mooncenApi";
import { theme } from "../constants/theme";
import {
  getCourseApplicationState,
  type EffectiveCourseStatus,
} from "./courseStatus";

export type CourseStatusMeta = {
  code: EffectiveCourseStatus;
  label: string;
  color: string;
  backgroundColor: string;
};

const STATUS_META: Record<EffectiveCourseStatus, Omit<CourseStatusMeta, "code">> = {
  SCHEDULED: {
    label: "접수예정",
    color: theme.colors.warning,
    backgroundColor: theme.colors.warningSoft,
  },
  OPEN: {
    label: "접수중",
    color: theme.colors.success,
    backgroundColor: theme.colors.mintSoft,
  },
  WAITING: {
    label: "대기접수",
    color: theme.colors.secondary,
    backgroundColor: theme.colors.secondarySoft,
  },
  DEADLINE: {
    label: "마감임박",
    color: theme.colors.danger,
    backgroundColor: theme.colors.accentSoft,
  },
  CLOSED: {
    label: "접수마감",
    color: theme.colors.closed,
    backgroundColor: theme.colors.surfaceMuted,
  },
  UNKNOWN: {
    label: "상태확인",
    color: theme.colors.textMuted,
    backgroundColor: theme.colors.surfaceMuted,
  },
};

export function getCourseStatusMeta(course: CourseDto): CourseStatusMeta {
  const code = getCourseApplicationState(course).status;
  return { code, ...STATUS_META[code] };
}

export function getScopeLabel(scope: CourseScope | undefined): string {
  if (scope === "provider") return "문화센터";
  if (scope === "experience") return "전시·체험";
  if (scope === "education") return "평생교육";
  return "전체";
}

export function getCourseDomainLabel(course: CourseDto): string {
  const serviceGroup = course.service_group?.trim();
  if (serviceGroup === "문화센터") return "문화센터";
  if (serviceGroup === "체험") return "전시·체험";
  if (serviceGroup === "공공강좌") return "평생교육";
  return (
    course.program_type?.trim() ||
    course.collection_category?.trim() ||
    course.standard_category?.trim() ||
    "프로그램"
  );
}

export function getCourseVenue(course: CourseDto): string {
  return (
    course.venue_name?.trim() ||
    course.branch?.name.trim() ||
    course.provider_label?.trim() ||
    "장소 확인 필요"
  );
}

export function getCourseProvider(course: CourseDto): string {
  return course.provider_label?.trim() || course.branch?.provider_label?.trim() || course.provider;
}

export function getCourseAddress(course: CourseDto): string | null {
  return course.venue_address?.trim() || course.branch?.address?.trim() || null;
}

export function getCourseApplicationMethod(
  course: Pick<CourseDto, "application_method_raw" | "application_type">,
): string | null {
  const rawMethod = course.application_method_raw?.trim();
  if (rawMethod) return rawMethod;

  const rawType = course.application_type?.trim();
  if (!rawType) return null;
  const code = rawType.toUpperCase();
  if (code.includes("IDENTITY_REQUIRED")) return "온라인 신청(본인인증 필요)";
  if (code.includes("LOGIN_REQUIRED")) return "온라인 신청(로그인 필요)";
  if (code.includes("WAITLIST")) return "대기 신청";
  if (code.includes("PHONE")) return "전화 신청";
  if (code.includes("OFFLINE") || code === "IN_PERSON") return "방문 신청";
  if (code.includes("EXTERNAL")) return "외부 사이트 신청";
  if (code.includes("ONLINE") || code === "FACILITY_RESERVATION") return "온라인 신청";
  if (code.includes("INFO") || code === "NOTICE") return "신청 방법 확인";
  return /[ㄱ-힣]/.test(rawType) ? rawType : null;
}

export function formatCourseFee(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "수강료 확인";
  if (value <= 0) return "무료";
  return `${Math.round(value).toLocaleString("ko-KR")}원`;
}

export function formatMaterialFee(value: number | null): string | null {
  if (value === null || !Number.isFinite(value) || value <= 0) return null;
  return `${Math.round(value).toLocaleString("ko-KR")}원`;
}

export function formatCourseCapacity(
  current: number | null,
  total: number | null,
): string | null {
  if (total === null || !Number.isFinite(total) || total < 0) return null;
  const totalCount = Math.round(total).toLocaleString("ko-KR");
  if (current === null || !Number.isFinite(current) || current < 0) {
    return `정원 ${totalCount}명`;
  }
  return `${Math.round(current).toLocaleString("ko-KR")} / ${totalCount}명`;
}

export function formatCourseHeadcount(value: number | null): string | null {
  if (value === null || !Number.isFinite(value) || value < 0) return null;
  return `${Math.round(value).toLocaleString("ko-KR")}명`;
}

export function formatCourseDate(value: string | null): string | null {
  if (!value) return null;
  const parsed = dayjs(value);
  return parsed.isValid() ? parsed.format("YYYY.MM.DD") : null;
}

export function formatCourseDateRange(
  start: string | null,
  end: string | null,
): string | null {
  const formattedStart = formatCourseDate(start);
  const formattedEnd = formatCourseDate(end);
  if (formattedStart && formattedEnd) return `${formattedStart} - ${formattedEnd}`;
  if (formattedStart) return `${formattedStart}부터`;
  if (formattedEnd) return `${formattedEnd}까지`;
  return null;
}

export function getCourseSchedule(course: CourseDto): string {
  return (
    course.schedule_summary?.trim() ||
    course.day_schedule?.trim() ||
    course.schedule_raw?.trim() ||
    formatCourseDateRange(course.start_date, course.end_date) ||
    "일정 확인 필요"
  );
}

export function getCourseTarget(course: CourseDto): string {
  return (
    course.target?.trim() ||
    course.eligibility_raw?.trim() ||
    course.target_tags.join(" · ") ||
    "대상 확인 필요"
  );
}

export function getSafeRemoteUrl(value: string | null | undefined): string | null {
  const trimmed = value?.trim();
  if (!trimmed) return null;
  try {
    const parsed = new URL(trimmed);
    if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) {
      return null;
    }
    return parsed.toString();
  } catch {
    return null;
  }
}

export function getCourseImageUrl(course: CourseDto): string | null {
  return getSafeRemoteUrl(course.image_url);
}

export function getCourseSourceUrl(course: CourseDto): string | null {
  return getSafeRemoteUrl(course.raw_url);
}
