import type { Branch, Course } from '../api';

export type MapMode = 'provider' | 'education' | 'experience';

const cultureCenterProviders = new Set([
  'HOMEPLUS',
  'LOTTE',
  'EMART',
  'HYUNDAI_DEPT',
  'GALLERIA',
  'AK_PLAZA',
  'ELAND_RETAIL',
  'SHINSEGAE_ACADEMY',
  'LOTTE_MART',
]);
const educationServiceGroup = '공공강좌';
const experienceServiceGroup = '체험';
const experienceCategoryTokens = [
  '체험',
  '견학',
  '탐방',
  '전시',
  '공연',
  '관람',
  '해설',
  '행사',
  '캠프',
  '문화기반시설',
  '문화재단',
  '박물관',
  '미술관',
  '과학관',
  '수목원',
  '생태',
  '야외',
  '예술',
  '예술/공연',
];
const experienceContentTokens = [
  '체험',
  '견학',
  '탐방',
  '전시',
  '공연',
  '관람',
  '행사',
  '캠프',
];
const experienceProgramTypes = new Set(['체험', '견학', '탐방', '전시', '공연', '관람', '캠프']);
const experienceExcludedProgramTypes = new Set(['숙박', '대관']);
const experienceSourceGroups = new Set([
  'library',
  'museum_science',
  'science_museum',
  'museum',
  'arts_culture',
  'national_institution',
  'sports_facility',
  'sports_reservation',
  'welfare',
  'youth',
  'arboretum_ecology',
]);
const localGovernmentEducationBranchTokens = [
  '주민센터',
  '주민자치',
  '행정복지센터',
  '행정복지센타',
  '동사무소',
  '읍사무소',
  '면사무소',
  '자치회관',
];
const localGovernmentEducationOfficeTokenRules = [
  { token: '시청', falseFragments: ['시청각', '시청자', '시청소년', '시청년'] },
  { token: '군청', falseFragments: ['군청소년', '군청년'] },
  { token: '구청', falseFragments: ['구청소년', '구청년'] },
];
const localGovernmentEducationExcludedFacilityTokens = [
  '도서관',
  '박물관',
  '미술관',
  '과학관',
  '문화회관',
  '문화센터',
  '문화재단',
  '문화의집',
  '문화공간',
  '아트센터',
  '체육관',
  '체육센터',
  '체육회관',
  '스포츠센터',
  '종합운동장',
  '복지관',
  '복지회관',
  '청소년수련관',
  '청소년센터',
  '청소년문화의집',
  '청년센터',
  '청년지원센터',
  '수련원',
  '공연장',
  '극장',
  '전시관',
  '수목원',
  '생태관',
];

function compactScopeValue(value: string | null | undefined) {
  return String(value || '').trim().replace(/\s+/g, '').toLowerCase();
}

function isExperienceCategoryValue(value: string | null | undefined) {
  const normalized = compactScopeValue(value);
  return Boolean(normalized) && experienceCategoryTokens.some((token) => normalized.includes(compactScopeValue(token)));
}

function isExperienceContentValue(value: string | null | undefined) {
  const normalized = compactScopeValue(value);
  return Boolean(normalized) && experienceContentTokens.some((token) => normalized.includes(compactScopeValue(token)));
}

function recordStringValue(value: unknown, key: string) {
  if (!value || typeof value !== 'object') return '';
  const field = (value as Record<string, unknown>)[key];
  return typeof field === 'string' ? field : '';
}

function branchFacilityValues(branch: Branch) {
  const operatorAddressBackfill = branch.basic_info?.operator_address_backfill;
  const educationInstitution = recordStringValue(branch.basic_info, 'education_institution');
  const operatorOfficeValues = [
    recordStringValue(operatorAddressBackfill, 'target_name'),
    recordStringValue(operatorAddressBackfill, 'matched_name'),
  ];
  return [
    branch.name,
    branch.facility_type,
    branch.facility_category,
    educationInstitution,
    ...operatorOfficeValues,
  ]
    .map((value) => compactScopeValue(value))
    .filter(Boolean);
}

function branchHasFacilityToken(branch: Branch, tokens: string[]) {
  const values = branchFacilityValues(branch);
  return tokens.some((token) => {
    const normalizedToken = compactScopeValue(token);
    return values.some((value) => value.includes(normalizedToken));
  });
}

export function isLocalGovernmentEducationBranch(branch: Branch) {
  const values = branchFacilityValues(branch);
  const educationInstitution = compactScopeValue(
    recordStringValue(branch.basic_info, 'education_institution'),
  );
  if (values.length === 0) return false;
  if (branchHasFacilityToken(branch, localGovernmentEducationExcludedFacilityTokens)) return false;
  if (branchHasFacilityToken(branch, localGovernmentEducationBranchTokens)) return true;
  if (/^[가-힣0-9]{1,40}(시|군|구|읍|면|동)$/.test(educationInstitution)) return true;
  return localGovernmentEducationOfficeTokenRules.some(({ token, falseFragments }) => {
    const normalizedToken = compactScopeValue(token);
    const normalizedFalseFragments = falseFragments.map(compactScopeValue);
    return values.some(
      (value) => value.includes(normalizedToken)
        && normalizedFalseFragments.every((fragment) => !value.includes(fragment)),
    );
  });
}

function isCultureCenterCourse(course: Course) {
  return cultureCenterProviders.has(course.provider);
}

export function branchCategories(branch: Branch) {
  const institutionText = `${branch.name || ''} ${branch.facility_category || ''} ${branch.facility_type || ''}`.trim();
  const facilityMetadataText = `${branch.facility_category || ''} ${branch.facility_type || ''}`.trim();
  const facilityAliases = [
    institutionText.includes('도서관') ? '도서관' : '',
    institutionText.includes('박물관') || institutionText.includes('미술관') ? '박물관' : '',
    institutionText.includes('과학관') ? '과학관' : '',
    institutionText.includes('문화재단') ? '문화재단' : '',
    institutionText.includes('수목원')
      || institutionText.includes('생태원')
      || institutionText.includes('생태공원')
      || institutionText.includes('정원')
      ? '자연·생태'
      : '',
    facilityMetadataText ? '체험' : '',
  ].filter(Boolean);
  return [
    branch.facility_service_group,
    branch.facility_collection_category,
    branch.facility_category,
    branch.facility_type,
    ...facilityAliases,
    branch.primary_service_group,
    ...(branch.service_groups || []),
    ...Object.keys(branch.service_group_counts || {}),
    branch.primary_collection_category,
    ...(branch.collection_categories || []),
    ...Object.keys(branch.category_counts || {}),
  ]
    .map((value) => String(value || '').trim())
    .filter(Boolean);
}

export function isFacilityInfoBranch(branch: Branch) {
  return branch.provider === 'CULTURE_FACILITY' || Boolean(branch.facility_source);
}

function isExperienceBranch(branch: Branch) {
  return (
    isFacilityInfoBranch(branch)
    || branchHasFacilityToken(branch, localGovernmentEducationExcludedFacilityTokens)
    || branchCategories(branch).some(isExperienceCategoryValue)
  );
}

function isCultureCenterBranch(branch: Branch) {
  return branchProviders(branch).some((provider) => cultureCenterProviders.has(provider));
}

function isExperienceCourse(course: Course) {
  const sourceGroup = String(course.source_group || '').trim().toLowerCase();
  const programType = String(course.program_type || '').trim();
  if (experienceExcludedProgramTypes.has(programType)) return false;
  const serviceGroup = compactScopeValue(course.service_group);
  const localGovernmentBranch = Boolean(
    course.branch && isLocalGovernmentEducationBranch(course.branch),
  );
  const institutionExperience = (
    course.provider === 'CULTURE_FACILITY'
    || experienceSourceGroups.has(sourceGroup)
    || Boolean(course.branch && isExperienceBranch(course.branch))
  );
  if (serviceGroup === compactScopeValue(educationServiceGroup)) {
    return !localGovernmentBranch && institutionExperience;
  }
  if (serviceGroup === compactScopeValue(experienceServiceGroup)) return true;
  return (
    institutionExperience ||
    experienceProgramTypes.has(programType) ||
    [
      course.service_group,
      course.collection_category,
      course.domain_category,
      course.standard_category,
      course.ai_category,
      course.source_group,
      course.operator_type,
    ].some(isExperienceCategoryValue) ||
    [course.title, course.title_raw, course.category_raw].some(isExperienceContentValue) ||
    ['/experience', '/exprn', '/exp/'].some((token) => String(course.raw_url || '').toLowerCase().includes(token)) ||
    Boolean(course.branch && isExperienceBranch(course.branch))
  );
}

export function courseMatchesMapMode(course: Course, mapMode: MapMode) {
  const cultureCenter = isCultureCenterCourse(course);
  if (mapMode === 'provider') return cultureCenter;
  if (cultureCenter) return false;
  if (mapMode === 'experience') return isExperienceCourse(course);
  return Boolean(
    course.branch
    && isLocalGovernmentEducationBranch(course.branch)
    && !isExperienceCourse(course),
  );
}

export function branchMatchesMapMode(branch: Branch, mapMode: MapMode) {
  const authoritativeCount = branchAuthoritativeScopeCourseCount(branch, mapMode);
  if (authoritativeCount != null) return authoritativeCount > 0;
  const cultureCenter = isCultureCenterBranch(branch);
  if (mapMode === 'provider') return cultureCenter;
  if (cultureCenter) return false;
  const educationCount = branchServiceGroupCourseCount(branch, educationServiceGroup);
  const experienceCount = branchServiceGroupCourseCount(branch, experienceServiceGroup);
  if (mapMode === 'education') {
    if (!isLocalGovernmentEducationBranch(branch)) return false;
    if (educationCount > 0) return true;
    if (experienceCount > 0) return false;
    return branchHasAnyCourse(branch);
  }
  if (experienceCount > 0) return true;
  if (educationCount > 0 && isLocalGovernmentEducationBranch(branch)) return false;
  return isExperienceBranch(branch);
}

/**
 * `undefined` means the server did not provide the scope contract. A present
 * zero is authoritative and must not fall back to frontend name heuristics.
 */
export function branchAuthoritativeScopeCourseCount(branch: Branch, mapMode: MapMode) {
  const counts = branch.scope_course_counts;
  if (!counts || !Object.prototype.hasOwnProperty.call(counts, mapMode)) return null;
  const count = Number(counts[mapMode]);
  return Number.isFinite(count) && count >= 0 ? count : null;
}

function sumBranchCounts(
  counts: Record<string, number> | undefined,
  predicate: (value: string) => boolean,
) {
  return Object.entries(counts || {}).reduce((sum, [category, count]) => (
    predicate(category) ? sum + Number(count || 0) : sum
  ), 0);
}

function branchHasAnyCourse(branch: Branch) {
  return Boolean(
    (branch.course_count ?? 0) > 0 ||
    (branch.active_course_count ?? 0) > 0 ||
    (branch.open_course_count ?? 0) > 0 ||
    sumBranchCounts(branch.category_counts, () => true) > 0 ||
    sumBranchCounts(branch.service_group_counts, () => true) > 0
  );
}

function branchServiceGroupCourseCount(branch: Branch, serviceGroup: string) {
  return sumBranchCounts(
    branch.service_group_counts,
    (value) => compactScopeValue(value) === compactScopeValue(serviceGroup),
  );
}

function branchHasPositiveServiceGroupCount(branch: Branch) {
  return sumBranchCounts(branch.service_group_counts, () => true) > 0;
}

export function branchSourceIds(branch: Branch | null | undefined) {
  if (!branch) return [];
  const sourceIds = branch.branch_ids?.length ? branch.branch_ids : [branch.id];
  return [...new Set(sourceIds.filter(Boolean))];
}

export function expandBranchIds(branchIds: string[], branches: Branch[]) {
  const branchById = new Map(branches.map((branch) => [branch.id, branch]));
  return [
    ...new Set(
      branchIds.flatMap((branchId) => branchSourceIds(branchById.get(branchId) || ({ id: branchId } as Branch))),
    ),
  ];
}

export function branchIdMatches(course: Course, branch: Branch | null | undefined) {
  const courseBranchId = course.branch_id || course.branch?.id;
  return Boolean(courseBranchId && branchSourceIds(branch).includes(courseBranchId));
}

export function branchProviders(branch: Branch) {
  return branch.providers?.length ? branch.providers : [branch.provider];
}

export function branchExperienceCourseCount(branch: Branch) {
  const serviceGroupCount = branchServiceGroupCourseCount(branch, experienceServiceGroup);
  if (serviceGroupCount > 0) return serviceGroupCount;
  const publicCourseCount = branchServiceGroupCourseCount(branch, educationServiceGroup);
  if (
    publicCourseCount > 0
    && !isLocalGovernmentEducationBranch(branch)
    && isExperienceBranch(branch)
  ) {
    return publicCourseCount;
  }
  if (branchHasPositiveServiceGroupCount(branch)) return 0;
  if (!branchHasAnyCourse(branch)) return 0;
  return isExperienceBranch(branch)
    ? branch.open_course_count ?? branch.active_course_count ?? branch.course_count ?? 0
    : 0;
}

export function branchMapModeCourseCount(branch: Branch, mapMode: MapMode) {
  const authoritativeCount = branchAuthoritativeScopeCourseCount(branch, mapMode);
  if (authoritativeCount != null) return authoritativeCount;
  if (!branchHasAnyCourse(branch)) return 0;
  if (mapMode === 'provider') return branch.open_course_count ?? 0;
  if (mapMode === 'experience') return branchExperienceCourseCount(branch);
  if (!isLocalGovernmentEducationBranch(branch)) return 0;
  const educationCount = branchServiceGroupCourseCount(branch, educationServiceGroup);
  if (educationCount > 0) return educationCount;
  if (branchHasPositiveServiceGroupCount(branch)) return 0;
  return branchMatchesMapMode(branch, 'education')
    ? branch.open_course_count ?? branch.active_course_count ?? branch.course_count ?? 0
    : 0;
}

export function isCultureProviderOnlySelection(providers: string[]) {
  return providers.length > 0 && providers.every((provider) => cultureCenterProviders.has(provider));
}

export function scopeModeLabel(mapMode: MapMode) {
  if (mapMode === 'provider') return '문화센터';
  if (mapMode === 'education') return '교육';
  return '체험';
}

export function resultScopeLabelFor(mapMode: MapMode) {
  if (mapMode === 'provider') return '조건에 맞는 문화센터 강좌';
  if (mapMode === 'education') return '조건에 맞는 교육 프로그램';
  return '조건에 맞는 체험 프로그램';
}

export function nearbyScopeTitle(mapMode: MapMode) {
  if (mapMode === 'provider') return '내 주변 문화센터';
  if (mapMode === 'education') return '내 주변 교육';
  return '내 주변 체험';
}
