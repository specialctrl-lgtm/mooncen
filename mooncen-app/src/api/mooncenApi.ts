export type CourseScope = "provider" | "education" | "experience";
export type CourseSort =
  | "latest"
  | "popular"
  | "deadline"
  | "start_date"
  | "price_asc"
  | "price_desc";

export type ScopeCourseCounts = {
  provider: number;
  education: number;
  experience: number;
};

export type BranchDto = {
  id: string;
  branch_ids: string[];
  providers: string[];
  name: string;
  provider: string;
  provider_label: string | null;
  branch_code: string | null;
  address: string | null;
  phone: string | null;
  lat: number | null;
  lon: number | null;
  course_count: number;
  active_course_count: number;
  open_course_count: number;
  collection_categories: string[];
  category_counts: Record<string, number>;
  primary_collection_category: string | null;
  service_groups: string[];
  service_group_counts: Record<string, number>;
  scope_course_counts: ScopeCourseCounts | null;
  primary_service_group: string | null;
  website_url: string | null;
  favicon_url: string | null;
  operating_hours: string | null;
  regular_holiday: string | null;
  admission_fee: string | null;
  facility_type: string | null;
  facility_category: string | null;
  facility_source: string | null;
  facility_source_sheet: string | null;
  facility_service_group: string | null;
  facility_collection_category: string | null;
  region_sido: string | null;
  region_sigungu: string | null;
  basic_info: Record<string, unknown> | null;
};

export type CourseDto = {
  id: string;
  provider: string;
  provider_label: string | null;
  provider_course_id: string | null;
  branch_id: string | null;
  title: string;
  title_raw: string | null;
  title_prefix_removed: string | null;
  instructor: string | null;
  fee: number | null;
  fee_status: "UNKNOWN" | "FREE" | "PAID";
  material_fee: number | null;
  sessions: number | null;
  start_date: string | null;
  end_date: string | null;
  apply_start: string | null;
  apply_end: string | null;
  apply_period_raw: string | null;
  capacity_total: number | null;
  capacity_current: number | null;
  capacity_remaining: number | null;
  waitlist_total: number | null;
  application_type: string | null;
  application_method_raw: string | null;
  reservation_available: boolean | null;
  discovery_status: string | null;
  eligibility_raw: string | null;
  status: string | null;
  status_label: string | null;
  target: string | null;
  target_age_group: string | null;
  target_min_age: number | null;
  target_max_age: number | null;
  target_age_is_explicit: boolean | null;
  target_tags: string[];
  category_raw: string | null;
  collection_category: string | null;
  domain_category: string | null;
  standard_category: string | null;
  source_group: string | null;
  operator_type: string | null;
  service_group: string | null;
  collection_type: string | null;
  program_type: string | null;
  schedule_raw: string | null;
  schedule_days: string[];
  schedule_dates: string[];
  schedule_time_start: string | null;
  schedule_time_end: string | null;
  schedule_summary: string | null;
  day_schedule: string | null;
  session_label: string | null;
  description: string | null;
  image_url: string | null;
  venue_name: string | null;
  venue_address: string | null;
  application_url: string | null;
  raw_url: string | null;
  view_count: number | null;
  is_active: boolean | null;
  first_seen_at: string | null;
  last_seen_at: string | null;
  removed_at: string | null;
  change_detected_at: string | null;
  created_at: string | null;
  ai_category: string | null;
  ai_tags: string[];
  ai_summary: string | null;
  ai_title_processed: boolean | null;
  ai_title_confidence: number | null;
  ai_title_result: Record<string, unknown> | null;
  branch: BranchDto | null;
};

export type CourseListResponse = {
  total: number;
  page: number;
  size: number;
  items: CourseDto[];
};

export type CourseListQuery = {
  page?: number;
  size?: number;
  keyword?: string;
  category?: string;
  collectionCategories?: string[];
  serviceGroups?: string[];
  domainCategories?: string[];
  scope?: CourseScope;
  provider?: string;
  branchId?: string;
  branchIds?: string[];
  lat?: number;
  lon?: number;
  radiusKm?: number;
  feeGroups?: string[];
  ageGroups?: string[];
  timeGroups?: string[];
  statuses?: string[];
  childAgeMonths?: number;
  minFee?: number;
  maxFee?: number;
  days?: string[];
  courseDate?: string;
  includeInactive?: boolean;
  excludeUnavailable?: boolean;
  sort?: CourseSort;
};

export type NearbyBranchesQuery = {
  lat: number;
  lon: number;
  radiusKm?: number;
  limit?: number;
  includeEmpty?: boolean;
};

export type ApiRequestOptions = {
  signal?: AbortSignal;
};

const DEFAULT_API_BASE_URL = "https://mooncen.kr";
const API_TIMEOUT_MS = 15_000;

function normalizedBaseUrl(value: string | undefined): string {
  const candidate = value?.trim() || DEFAULT_API_BASE_URL;
  try {
    const parsed = new URL(candidate);
    if (
      (parsed.protocol !== "http:" && parsed.protocol !== "https:") ||
      parsed.username ||
      parsed.password
    ) {
      return DEFAULT_API_BASE_URL;
    }
    parsed.search = "";
    parsed.hash = "";
    return parsed.toString().replace(/\/+$/, "");
  } catch {
    return DEFAULT_API_BASE_URL;
  }
}

export const MOONCEN_API_BASE_URL = normalizedBaseUrl(
  process.env.EXPO_PUBLIC_API_BASE_URL,
);

export class MooncenApiError extends Error {
  readonly status: number | null;
  readonly retryable: boolean;

  constructor(message: string, status: number | null = null, retryable = false) {
    super(message);
    this.name = "MooncenApiError";
    this.status = status;
    this.retryable = retryable;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function nullableString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function requiredString(value: unknown, fallback: string): string {
  return nullableString(value) ?? fallback;
}

function nullableNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function nonNegativeNumber(value: unknown, fallback = 0): number {
  const parsed = nullableNumber(value);
  return parsed !== null && parsed >= 0 ? parsed : fallback;
}

function nullableBoolean(value: unknown): boolean | null {
  if (typeof value === "boolean") return value;
  if (value === "true") return true;
  if (value === "false") return false;
  return null;
}

function stringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map(nullableString)
    .filter((item): item is string => item !== null);
}

function numberRecord(value: unknown): Record<string, number> {
  if (!isRecord(value)) return {};
  const entries = Object.entries(value).flatMap(([key, item]) => {
    const count = nullableNumber(item);
    return count !== null && count >= 0 ? [[key, count] as const] : [];
  });
  return Object.fromEntries(entries);
}

function nullableRecord(value: unknown): Record<string, unknown> | null {
  return isRecord(value) ? value : null;
}

function parseScopeCourseCounts(value: unknown): ScopeCourseCounts | null {
  if (!isRecord(value)) return null;
  return {
    provider: nonNegativeNumber(value.provider),
    education: nonNegativeNumber(value.education),
    experience: nonNegativeNumber(value.experience),
  };
}

function parseBranch(value: unknown): BranchDto {
  if (!isRecord(value)) {
    throw new MooncenApiError("지점 응답 형식이 올바르지 않습니다.");
  }
  const id = nullableString(value.id);
  if (!id) {
    throw new MooncenApiError("지점 응답에 식별자가 없습니다.");
  }

  return {
    id,
    branch_ids: stringArray(value.branch_ids),
    providers: stringArray(value.providers),
    name: requiredString(value.name, "지점명 미정"),
    provider: requiredString(value.provider, "UNKNOWN"),
    provider_label: nullableString(value.provider_label),
    branch_code: nullableString(value.branch_code),
    address: nullableString(value.address),
    phone: nullableString(value.phone),
    lat: nullableNumber(value.lat),
    lon: nullableNumber(value.lon),
    course_count: nonNegativeNumber(value.course_count),
    active_course_count: nonNegativeNumber(value.active_course_count),
    open_course_count: nonNegativeNumber(value.open_course_count),
    collection_categories: stringArray(value.collection_categories),
    category_counts: numberRecord(value.category_counts),
    primary_collection_category: nullableString(value.primary_collection_category),
    service_groups: stringArray(value.service_groups),
    service_group_counts: numberRecord(value.service_group_counts),
    scope_course_counts: parseScopeCourseCounts(value.scope_course_counts),
    primary_service_group: nullableString(value.primary_service_group),
    website_url: nullableString(value.website_url),
    favicon_url: nullableString(value.favicon_url),
    operating_hours: nullableString(value.operating_hours),
    regular_holiday: nullableString(value.regular_holiday),
    admission_fee: nullableString(value.admission_fee),
    facility_type: nullableString(value.facility_type),
    facility_category: nullableString(value.facility_category),
    facility_source: nullableString(value.facility_source),
    facility_source_sheet: nullableString(value.facility_source_sheet),
    facility_service_group: nullableString(value.facility_service_group),
    facility_collection_category: nullableString(value.facility_collection_category),
    region_sido: nullableString(value.region_sido),
    region_sigungu: nullableString(value.region_sigungu),
    basic_info: nullableRecord(value.basic_info),
  };
}

function parseCourse(value: unknown): CourseDto {
  if (!isRecord(value)) {
    throw new MooncenApiError("강좌 응답 형식이 올바르지 않습니다.");
  }
  const id = nullableString(value.id);
  if (!id) {
    throw new MooncenApiError("강좌 응답에 식별자가 없습니다.");
  }

  return {
    id,
    provider: requiredString(value.provider, "UNKNOWN"),
    provider_label: nullableString(value.provider_label),
    provider_course_id: nullableString(value.provider_course_id),
    branch_id: nullableString(value.branch_id),
    title: requiredString(value.title, "강좌명 미정"),
    title_raw: nullableString(value.title_raw),
    title_prefix_removed: nullableString(value.title_prefix_removed),
    instructor: nullableString(value.instructor),
    fee: nullableNumber(value.fee),
    fee_status:
      value.fee_status === "FREE" || value.fee_status === "PAID"
        ? value.fee_status
        : "UNKNOWN",
    material_fee: nullableNumber(value.material_fee),
    sessions: nullableNumber(value.sessions),
    start_date: nullableString(value.start_date),
    end_date: nullableString(value.end_date),
    apply_start: nullableString(value.apply_start),
    apply_end: nullableString(value.apply_end),
    apply_period_raw: nullableString(value.apply_period_raw),
    capacity_total: nullableNumber(value.capacity_total),
    capacity_current: nullableNumber(value.capacity_current),
    capacity_remaining: nullableNumber(value.capacity_remaining),
    waitlist_total: nullableNumber(value.waitlist_total),
    application_type: nullableString(value.application_type),
    application_method_raw: nullableString(value.application_method_raw),
    reservation_available: nullableBoolean(value.reservation_available),
    discovery_status: nullableString(value.discovery_status),
    eligibility_raw: nullableString(value.eligibility_raw),
    status: nullableString(value.status),
    status_label: nullableString(value.status_label),
    target: nullableString(value.target),
    target_age_group: nullableString(value.target_age_group),
    target_min_age: nullableNumber(value.target_min_age),
    target_max_age: nullableNumber(value.target_max_age),
    target_age_is_explicit: nullableBoolean(value.target_age_is_explicit),
    target_tags: stringArray(value.target_tags),
    category_raw: nullableString(value.category_raw),
    collection_category: nullableString(value.collection_category),
    domain_category: nullableString(value.domain_category),
    standard_category: nullableString(value.standard_category),
    source_group: nullableString(value.source_group),
    operator_type: nullableString(value.operator_type),
    service_group: nullableString(value.service_group),
    collection_type: nullableString(value.collection_type),
    program_type: nullableString(value.program_type),
    schedule_raw: nullableString(value.schedule_raw),
    schedule_days: stringArray(value.schedule_days),
    schedule_dates: stringArray(value.schedule_dates),
    schedule_time_start: nullableString(value.schedule_time_start),
    schedule_time_end: nullableString(value.schedule_time_end),
    schedule_summary: nullableString(value.schedule_summary),
    day_schedule: nullableString(value.day_schedule),
    session_label: nullableString(value.session_label),
    description: nullableString(value.description),
    image_url: nullableString(value.image_url),
    venue_name: nullableString(value.venue_name),
    venue_address: nullableString(value.venue_address),
    application_url: nullableString(value.application_url),
    raw_url: nullableString(value.raw_url),
    view_count: nullableNumber(value.view_count),
    is_active: nullableBoolean(value.is_active),
    first_seen_at: nullableString(value.first_seen_at),
    last_seen_at: nullableString(value.last_seen_at),
    removed_at: nullableString(value.removed_at),
    change_detected_at: nullableString(value.change_detected_at),
    created_at: nullableString(value.created_at),
    ai_category: nullableString(value.ai_category),
    ai_tags: stringArray(value.ai_tags),
    ai_summary: nullableString(value.ai_summary),
    ai_title_processed: nullableBoolean(value.ai_title_processed),
    ai_title_confidence: nullableNumber(value.ai_title_confidence),
    ai_title_result: nullableRecord(value.ai_title_result),
    branch: value.branch == null ? null : parseBranch(value.branch),
  };
}

function parseCourseList(value: unknown, query: CourseListQuery): CourseListResponse {
  if (!isRecord(value) || !Array.isArray(value.items)) {
    throw new MooncenApiError("강좌 목록 응답 형식이 올바르지 않습니다.");
  }
  const items = value.items.map(parseCourse);
  return {
    total: nonNegativeNumber(value.total, items.length),
    page: nonNegativeNumber(value.page, query.page ?? 1) || 1,
    size: nonNegativeNumber(value.size, query.size ?? 20) || 20,
    items,
  };
}

function parseErrorMessage(value: unknown, status: number): string {
  if (isRecord(value)) {
    const detail = nullableString(value.detail);
    if (detail) return detail.slice(0, 200);
  }
  if (status === 404) return "요청한 정보를 찾을 수 없습니다.";
  if (status >= 500) return "서버에서 정보를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.";
  return `요청을 처리하지 못했습니다. (${status})`;
}

async function requestJson(path: string, options: ApiRequestOptions): Promise<unknown> {
  let response: Response;
  let body: string;
  let timeoutId: ReturnType<typeof setTimeout> | undefined;
  let didTimeout = false;
  const requestController = new AbortController();
  const forwardExternalAbort = () => requestController.abort();

  if (options.signal?.aborted) {
    requestController.abort();
  } else {
    options.signal?.addEventListener("abort", forwardExternalAbort, { once: true });
  }

  try {
    ({ response, body } = await Promise.race([
      (async () => {
        const fetchedResponse = await fetch(`${MOONCEN_API_BASE_URL}${path}`, {
          headers: { Accept: "application/json" },
          signal: requestController.signal,
        });
        const fetchedBody = await fetchedResponse.text();
        return { response: fetchedResponse, body: fetchedBody };
      })(),
      new Promise<never>((_resolve, reject) => {
        timeoutId = setTimeout(() => {
          didTimeout = true;
          requestController.abort();
          reject(new MooncenApiError("서버 응답 시간이 초과되었습니다. 다시 시도해 주세요."));
        }, API_TIMEOUT_MS);
      }),
    ]));
  } catch (error) {
    if (options.signal?.aborted || (error instanceof Error && error.name === "AbortError")) {
      if (didTimeout && !options.signal?.aborted) {
        throw new MooncenApiError("서버 응답 시간이 초과되었습니다. 다시 시도해 주세요.");
      }
      throw error;
    }
    if (error instanceof MooncenApiError) throw error;
    throw new MooncenApiError(
      "네트워크 연결을 확인하고 다시 시도해 주세요.",
      null,
      true,
    );
  } finally {
    if (timeoutId !== undefined) clearTimeout(timeoutId);
    options.signal?.removeEventListener("abort", forwardExternalAbort);
  }

  let payload: unknown = null;
  if (body.trim()) {
    try {
      payload = JSON.parse(body) as unknown;
    } catch {
      if (response.ok) {
        throw new MooncenApiError("서버 응답을 읽을 수 없습니다.", response.status);
      }
    }
  }

  if (!response.ok) {
    throw new MooncenApiError(parseErrorMessage(payload, response.status), response.status);
  }
  if (payload === null) {
    throw new MooncenApiError("서버 응답이 비어 있습니다.", response.status);
  }
  return payload;
}

function setText(params: URLSearchParams, name: string, value: string | undefined): void {
  const normalized = value?.trim();
  if (normalized) params.set(name, normalized);
}

function setNumber(params: URLSearchParams, name: string, value: number | undefined): void {
  if (value !== undefined && Number.isFinite(value)) params.set(name, String(value));
}

function setCsv(params: URLSearchParams, name: string, values: string[] | undefined): void {
  const normalized = values?.map((value) => value.trim()).filter(Boolean);
  if (normalized?.length) params.set(name, normalized.join(","));
}

function courseListPath(query: CourseListQuery): string {
  const params = new URLSearchParams();
  setNumber(params, "page", query.page ?? 1);
  setNumber(params, "size", query.size ?? 20);
  setText(params, "keyword", query.keyword);
  setText(params, "category", query.category);
  setCsv(params, "collection_category", query.collectionCategories);
  setCsv(params, "service_group", query.serviceGroups);
  setCsv(params, "domain_category", query.domainCategories);
  setText(params, "scope", query.scope);
  setText(params, "provider", query.provider);
  setText(params, "branch_id", query.branchId);
  setCsv(params, "branch_ids", query.branchIds);
  setNumber(params, "lat", query.lat);
  setNumber(params, "lon", query.lon);
  setNumber(params, "radius_km", query.radiusKm);
  setCsv(params, "fee_groups", query.feeGroups);
  setCsv(params, "age_groups", query.ageGroups);
  setCsv(params, "time_groups", query.timeGroups);
  setCsv(params, "statuses", query.statuses);
  setNumber(params, "child_age_months", query.childAgeMonths);
  setNumber(params, "min_fee", query.minFee);
  setNumber(params, "max_fee", query.maxFee);
  setCsv(params, "days", query.days);
  setText(params, "course_date", query.courseDate);
  if (query.includeInactive) params.set("include_inactive", "true");
  if (query.excludeUnavailable) params.set("exclude_unavailable", "true");
  setText(params, "sort", query.sort);
  return `/api/courses/?${params.toString()}`;
}

function validateCourseListQuery(query: CourseListQuery): void {
  if (query.page !== undefined && (!Number.isInteger(query.page) || query.page < 1 || query.page > 1000)) {
    throw new MooncenApiError("페이지 번호는 1에서 1000 사이여야 합니다.");
  }
  if (query.size !== undefined && (!Number.isInteger(query.size) || query.size < 1 || query.size > 100)) {
    throw new MooncenApiError("페이지 크기는 1에서 100 사이여야 합니다.");
  }

  const coordinateValues = [query.lat, query.lon, query.radiusKm];
  const hasRadiusFilter = coordinateValues.some((value) => value !== undefined);
  const hasCompleteRadiusFilter = coordinateValues.every((value) => value !== undefined);
  if (hasRadiusFilter && !hasCompleteRadiusFilter) {
    throw new MooncenApiError("반경 검색에는 위도, 경도, 반경이 모두 필요합니다.");
  }
  if (hasCompleteRadiusFilter) {
    const { lat, lon, radiusKm } = query as Required<
      Pick<CourseListQuery, "lat" | "lon" | "radiusKm">
    >;
    if (
      !Number.isFinite(lat) ||
      !Number.isFinite(lon) ||
      !Number.isFinite(radiusKm) ||
      lat < -90 ||
      lat > 90 ||
      lon < -180 ||
      lon > 180 ||
      radiusKm < 0.1 ||
      radiusKm > 30
    ) {
      throw new MooncenApiError("유효한 위치와 0.1km에서 30km 사이의 반경이 필요합니다.");
    }
  }
  if (
    query.childAgeMonths !== undefined &&
    (!Number.isInteger(query.childAgeMonths) || query.childAgeMonths < 0 || query.childAgeMonths > 1800)
  ) {
    throw new MooncenApiError("연령 개월 수는 0에서 1800 사이여야 합니다.");
  }
  if (
    (query.minFee !== undefined && (!Number.isFinite(query.minFee) || query.minFee < 0)) ||
    (query.maxFee !== undefined && (!Number.isFinite(query.maxFee) || query.maxFee < 0)) ||
    (query.minFee !== undefined && query.maxFee !== undefined && query.minFee > query.maxFee)
  ) {
    throw new MooncenApiError("유효한 수강료 범위를 입력해 주세요.");
  }
}

export async function fetchCourses(
  query: CourseListQuery = {},
  options: ApiRequestOptions = {},
): Promise<CourseListResponse> {
  validateCourseListQuery(query);
  return parseCourseList(await requestJson(courseListPath(query), options), query);
}

export async function fetchCourse(
  courseId: string,
  options: ApiRequestOptions = {},
): Promise<CourseDto> {
  const normalizedId = courseId.trim();
  if (!normalizedId) throw new MooncenApiError("강좌 식별자가 필요합니다.");
  return parseCourse(
    await requestJson(`/api/courses/${encodeURIComponent(normalizedId)}`, options),
  );
}

export async function fetchNearbyBranches(
  query: NearbyBranchesQuery,
  options: ApiRequestOptions = {},
): Promise<BranchDto[]> {
  if (
    !Number.isFinite(query.lat) ||
    !Number.isFinite(query.lon) ||
    query.lat < -90 ||
    query.lat > 90 ||
    query.lon < -180 ||
    query.lon > 180
  ) {
    throw new MooncenApiError("유효한 위도와 경도가 필요합니다.");
  }

  const radiusKm = query.radiusKm ?? 30;
  const limit = query.limit ?? 2000;
  if (!Number.isFinite(radiusKm) || radiusKm < 0.1 || radiusKm > 30) {
    throw new MooncenApiError("검색 반경은 0.1km에서 30km 사이여야 합니다.");
  }
  if (!Number.isInteger(limit) || limit < 1 || limit > 2000) {
    throw new MooncenApiError("지점 조회 수는 1에서 2000 사이여야 합니다.");
  }

  const params = new URLSearchParams({
    lat: String(query.lat),
    lon: String(query.lon),
    radius_km: String(radiusKm),
    limit: String(limit),
  });
  if (query.includeEmpty) params.set("include_empty", "true");
  const payload = await requestJson(`/api/branches/nearby?${params.toString()}`, options);
  if (!Array.isArray(payload)) {
    throw new MooncenApiError("주변 지점 응답 형식이 올바르지 않습니다.");
  }
  return payload.map(parseBranch);
}
