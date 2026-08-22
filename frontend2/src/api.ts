import { clearStoredUser, getCsrfToken } from './auth';
import { fetchWithTimeout } from './utils/fetchWithTimeout';

export type Branch = {
  id: string;
  branch_ids?: string[];
  providers?: string[];
  name: string;
  provider: string;
  provider_label?: string | null;
  branch_code?: string | null;
  address?: string | null;
  phone?: string | null;
  lat?: number | null;
  lon?: number | null;
  course_count?: number;
  active_course_count?: number;
  open_course_count?: number;
  collection_categories?: string[];
  category_counts?: Record<string, number>;
  primary_collection_category?: string | null;
  service_groups?: string[];
  service_group_counts?: Record<string, number>;
  scope_course_counts?: Partial<Record<'provider' | 'education' | 'experience', number>> | null;
  primary_service_group?: string | null;
  website_url?: string | null;
  favicon_url?: string | null;
  operating_hours?: string | null;
  regular_holiday?: string | null;
  admission_fee?: string | null;
  facility_type?: string | null;
  facility_category?: string | null;
  facility_source?: string | null;
  facility_source_sheet?: string | null;
  facility_service_group?: string | null;
  facility_collection_category?: string | null;
  region_sido?: string | null;
  region_sigungu?: string | null;
  basic_info?: Record<string, unknown> | null;
};

export type ProviderMeta = {
  provider: string;
  label: string;
  marker_label: string;
  marker_color: string;
  branch_count: number;
  coordinate_count: number;
  course_count: number;
  active_course_count: number;
  open_course_count: number;
};

export type Course = {
  id: string;
  provider: string;
  provider_label?: string | null;
  provider_course_id?: string | null;
  branch_id?: string | null;
  title: string;
  title_raw?: string | null;
  title_prefix_removed?: string | null;
  instructor?: string | null;
  fee?: number | null;
  fee_status?: 'UNKNOWN' | 'FREE' | 'PAID';
  material_fee?: number | null;
  sessions?: number | null;
  start_date?: string | null;
  end_date?: string | null;
  apply_start?: string | null;
  apply_end?: string | null;
  apply_period_raw?: string | null;
  capacity_total?: number | null;
  capacity_current?: number | null;
  capacity_remaining?: number | null;
  waitlist_total?: number | null;
  application_type?: string | null;
  application_method_raw?: string | null;
  reservation_available?: boolean | null;
  eligibility_raw?: string | null;
  status?: string | null;
  status_label?: string | null;
  target?: string | null;
  target_age_group?: string | null;
  target_min_age?: number | null;
  target_max_age?: number | null;
  target_tags?: string[];
  category_raw?: string | null;
  collection_category?: string | null;
  domain_category?: string | null;
  standard_category?: string | null;
  source_group?: string | null;
  operator_type?: string | null;
  service_group?: string | null;
  collection_type?: string | null;
  program_type?: string | null;
  schedule_raw?: string | null;
  schedule_days?: string[];
  schedule_dates?: string[];
  schedule_summary?: string | null;
  day_schedule?: string | null;
  session_label?: string | null;
  description?: string | null;
  image_url?: string | null;
  venue_name?: string | null;
  venue_address?: string | null;
  application_url?: string | null;
  raw_url?: string | null;
  ai_category?: string | null;
  ai_tags?: string[];
  ai_summary?: string | null;
  ai_title_processed?: boolean;
  ai_title_confidence?: number | null;
  ai_title_result?: {
    clean_title?: string | null;
    target_text?: string | null;
    confidence?: number | null;
    [key: string]: unknown;
  } | null;
  popularity_score?: number | null;
  favorite_count?: number | null;
  view_count?: number | null;
  created_at?: string | null;
  updated_at?: string | null;
  branch?: Branch | null;
};

export type CourseListResponse = {
  total: number;
  page: number;
  size: number;
  items: Course[];
};

type CourseQuery = {
  page?: number;
  size?: number;
  keyword?: string;
  category?: string;
  collectionCategories?: string[];
  serviceGroups?: string[];
  scope?: 'provider' | 'experience' | 'education';
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
  sort?: 'popular' | 'latest' | 'deadline' | 'start_date' | 'price_asc' | 'price_desc';
};

export type CourseMarkType = 'favorite' | 'applied';

export type CourseMarks = {
  favorite_course_ids: string[];
  applied_course_ids: string[];
};

export type CourseUpdateRequest = {
  id: string;
  course_id: string;
  reason: string;
  status: string;
  source_url?: string | null;
  request_count: number;
  requested_at: string;
  expires_at: string;
};

export type BugReportPayload = {
  title: string;
  content: string;
  page_url: string;
  user_agent: string;
  viewport: string;
  image_filename: string | null;
  image_media_type: string | null;
  image_base64: string | null;
};

export type BugReportResponse = {
  id?: string;
  status?: string;
  message?: string;
};

export type UserNotification = {
  id: string;
  course_id: string;
  notification_type: 'OPEN' | 'START' | 'DEADLINE' | 'COURSE_START' | string;
  title: string;
  message: string;
  priority: number;
  event_date?: string | null;
  mark_type: CourseMarkType;
  course: Course;
};

export type UserNotificationsResponse = {
  total: number;
  unread_count: number;
  items: UserNotification[];
};

async function requestJson<T>(path: string, options: RequestInit = {}, timeoutMs = 12_000): Promise<T> {
  const response = await fetchWithTimeout(path, options, timeoutMs);
  const contentType = response.headers.get('content-type') || '';
  if (!response.ok) {
    if (response.status === 401) {
      clearStoredUser();
      window.dispatchEvent(new Event('mooncen:auth-expired'));
      throw new Error('로그인이 만료되었습니다. 다시 로그인해 주세요.');
    }
    const payload = contentType.includes('application/json')
      ? await response.json().catch(() => null) as { detail?: unknown } | null
      : null;
    const detail = typeof payload?.detail === 'string' && response.status < 500 ? `: ${payload.detail.slice(0, 120)}` : '';
    throw new Error(`${response.status} ${response.statusText}${detail}`);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  if (!contentType.includes('application/json')) {
    const body = await response.text().catch(() => '');
    const hint = body.trim().startsWith('<') ? 'API 대신 HTML이 응답되었습니다. 프록시/nginx 라우팅을 확인하세요.' : 'JSON 응답이 아닙니다.';
    throw new Error(`${hint} path=${path} content-type=${contentType || 'unknown'}`);
  }
  return response.json() as Promise<T>;
}

function authOptions(options: RequestInit = {}): RequestInit {
  const headers = new Headers(options.headers);
  const method = String(options.method || 'GET').toUpperCase();
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    headers.set('X-CSRF-Token', getCsrfToken());
  }

  return {
    ...options,
    headers,
    credentials: 'include',
  };
}

export function fetchCourses(query: CourseQuery = {}, options: RequestInit = {}) {
  const params = new URLSearchParams();
  params.set('page', String(query.page ?? 1));
  params.set('size', String(query.size ?? 40));

  if (query.keyword) params.set('keyword', query.keyword);
  if (query.category && query.category !== 'all') params.set('category', query.category);
  if (query.collectionCategories?.length) params.set('collection_category', query.collectionCategories.join(','));
  if (query.serviceGroups?.length) params.set('service_group', query.serviceGroups.join(','));
  if (query.scope) params.set('scope', query.scope);
  if (query.provider) params.set('provider', query.provider);
  if (query.branchId) params.set('branch_id', query.branchId);
  if (query.branchIds?.length) params.set('branch_ids', query.branchIds.join(','));
  if (query.lat != null && query.lon != null && query.radiusKm != null) {
    params.set('lat', String(query.lat));
    params.set('lon', String(query.lon));
    params.set('radius_km', String(query.radiusKm));
  }
  if (query.feeGroups?.length) params.set('fee_groups', query.feeGroups.join(','));
  if (query.ageGroups?.length) params.set('age_groups', query.ageGroups.join(','));
  if (query.timeGroups?.length) params.set('time_groups', query.timeGroups.join(','));
  if (query.statuses?.length) params.set('statuses', query.statuses.join(','));
  if (query.childAgeMonths != null) params.set('child_age_months', String(query.childAgeMonths));
  if (query.minFee != null) params.set('min_fee', String(query.minFee));
  if (query.maxFee != null) params.set('max_fee', String(query.maxFee));
  if (query.days?.length) params.set('days', query.days.join(','));
  if (query.courseDate) params.set('course_date', query.courseDate);
  if (query.includeInactive) params.set('include_inactive', 'true');
  if (query.sort) params.set('sort', query.sort);

  return requestJson<CourseListResponse>(`/api/courses/?${params.toString()}`, options);
}

export function fetchCourse(courseId: string) {
  return requestJson<Course>(`/api/courses/${encodeURIComponent(courseId)}`);
}

export function requestCourseUpdate(courseId: string, reason = 'click', sourceUrl?: string | null) {
  return requestJson<CourseUpdateRequest>(`/api/courses/${encodeURIComponent(courseId)}/update-request`, authOptions({
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason, source_url: sourceUrl || undefined }),
  }));
}

export function submitBugReport(payload: BugReportPayload) {
  return requestJson<BugReportResponse>('/api/bug-reports', authOptions({
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }), 30_000);
}

export function fetchNearbyBranches(lat = 37.5665, lon = 126.978, radiusKm = 30, includeEmpty = false) {
  const limitedRadiusKm = Math.min(Math.max(radiusKm, 0.1), 30);
  const params = new URLSearchParams({
    lat: String(lat),
    lon: String(lon),
    radius_km: String(limitedRadiusKm),
    limit: '2000',
  });
  if (includeEmpty) params.set('include_empty', 'true');

  // A 30 km search can aggregate well over 100,000 current courses. Keep the
  // normal 12 s API budget elsewhere, but allow this explicitly bounded map
  // request to finish after the backend's per-statement safeguards.
  return requestJson<Branch[]>(`/api/branches/nearby?${params.toString()}`, {}, 30_000);
}

export function fetchProviders() {
  return requestJson<ProviderMeta[]>('/api/branches/providers');
}

export function fetchCourseMarks() {
  return requestJson<CourseMarks>('/api/users/me/course-marks', authOptions());
}

export function saveCourseMark(courseId: string, markType: CourseMarkType) {
  return requestJson<CourseMarks>(
    `/api/users/me/course-marks/${courseId}`,
    authOptions({
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mark_type: markType }),
    }),
  );
}

export function removeCourseMark(courseId: string, markType: CourseMarkType) {
  const params = new URLSearchParams({ mark_type: markType });
  return requestJson<CourseMarks>(
    `/api/users/me/course-marks/${courseId}?${params.toString()}`,
    authOptions({ method: 'DELETE' }),
  );
}

export function fetchMarkedCourses(markType: CourseMarkType, page = 1, size = 200) {
  const params = new URLSearchParams({
    mark_type: markType,
    page: String(page),
    size: String(size),
  });
  return requestJson<CourseListResponse>(`/api/users/me/courses?${params.toString()}`, authOptions());
}

export function fetchUserNotifications(limit = 30) {
  const params = new URLSearchParams({ limit: String(limit) });
  return requestJson<UserNotificationsResponse>(`/api/users/me/notifications?${params.toString()}`, authOptions());
}
