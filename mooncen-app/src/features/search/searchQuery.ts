import type { CourseListQuery, CourseSort } from "../../api/mooncenApi";
import type {
  FeeGroup,
  ProgramFilter,
  ScopeFilter,
  TimeGroup,
} from "../../stores/filterStore";

const AGE_GROUP_QUERY_VALUES: Partial<Record<ProgramFilter, string>> = {
  INFANT: "영유아",
  CHILD: "CHILD",
  TEEN: "TEEN",
  ADULT: "ADULT",
  SENIOR: "SENIOR",
};

export function buildCourseSearchQuery(
  searchText: string,
  scope: ScopeFilter,
  filters: ProgramFilter[],
  sort: CourseSort,
  branchId?: string,
  days: string[] = [],
  timeGroups: TimeGroup[] = [],
  feeGroups: FeeGroup[] = [],
): Omit<CourseListQuery, "page"> {
  const ageGroups = filters
    .map((filter) => AGE_GROUP_QUERY_VALUES[filter])
    .filter((value): value is string => value !== undefined);
  const onlyAvailable = filters.includes("OPEN");

  return {
    size: 30,
    keyword: searchText.length >= 2 ? searchText : undefined,
    branchId,
    scope: scope === "all" ? undefined : scope,
    statuses: onlyAvailable ? ["OPEN", "DEADLINE"] : undefined,
    excludeUnavailable: onlyAvailable,
    feeGroups: Array.from(new Set([
      ...(filters.includes("FREE") ? ["free"] : []),
      ...feeGroups,
    ])),
    ageGroups,
    days,
    timeGroups,
    sort,
  };
}
