import type { Branch, Course } from '../api';
import { branchCoordinates } from './branchCoordinates';
import { distanceKm, type UserLocation } from './location';
import {
  branchCategories,
  branchMapModeCourseCount,
  branchMatchesMapMode,
  branchProviders,
  branchSourceIds,
  isFacilityInfoBranch,
  type MapMode,
} from './mapScope';

function cleanValues(values: Array<string | null | undefined>) {
  return [...new Set(values.map((value) => String(value || '').trim()).filter(Boolean))];
}

function aliasesFor(branch: Branch, extraId?: string | null) {
  return cleanValues([branch.id, ...(branch.branch_ids || []), extraId]);
}

function normalizedName(value: string | null | undefined) {
  return String(value || '').trim().replace(/\s+/g, '').toLocaleLowerCase('ko-KR');
}

function physicalBranchKey(branch: Branch) {
  const coordinates = branchCoordinates(branch);
  const name = normalizedName(branch.name);
  if (!coordinates || !name) return '';
  return `${name}\u001f${coordinates.lat.toFixed(5)}\u001f${coordinates.lon.toFixed(5)}`;
}

function normalizeCoordinates(branch: Branch): Branch {
  const coordinates = branchCoordinates(branch);
  if (!coordinates) return branch;
  if (branch.lat === coordinates.lat && branch.lon === coordinates.lon) return branch;
  return { ...branch, lat: coordinates.lat, lon: coordinates.lon };
}

function mergeValues(current: string[] | undefined, extra: Array<string | null | undefined>) {
  return cleanValues([...(current || []), ...extra]);
}

function courseCategories(course: Course) {
  return cleanValues([
    course.collection_category,
    course.domain_category,
    course.standard_category,
    course.ai_category,
    course.program_type,
    course.source_group,
  ]);
}

function courseServiceGroups(course: Course) {
  return cleanValues([course.service_group]);
}

type BranchEvidence = {
  courseIds: Set<string>;
  openCourseIds: Set<string>;
  categories: Map<string, Set<string>>;
  serviceGroups: Map<string, Set<string>>;
};

function newEvidence(): BranchEvidence {
  return {
    courseIds: new Set(),
    openCourseIds: new Set(),
    categories: new Map(),
    serviceGroups: new Map(),
  };
}

function recordEvidence(target: Map<string, Set<string>>, values: string[], courseId: string) {
  values.forEach((value) => {
    const ids = target.get(value) ?? new Set<string>();
    ids.add(courseId);
    target.set(value, ids);
  });
}

function mergeEvidenceCounts(
  existing: Record<string, number> | undefined,
  evidence: Map<string, Set<string>>,
) {
  const result = { ...(existing || {}) };
  evidence.forEach((courseIds, key) => {
    result[key] = Math.max(Number(result[key] || 0), courseIds.size);
  });
  return result;
}

/**
 * Reconciles the independently loaded nearby-branch and course-search feeds.
 *
 * A course card is allowed to carry a perfectly usable branch and coordinate
 * even when the nearby endpoint omitted that row (for example, stale PostGIS
 * location data or different status aggregation). Those branches must still be
 * map candidates. Source branch aliases and physical identity prevent duplicate
 * markers when the nearby endpoint has already merged provider rows.
 */
export function reconcileMapBranches(nearbyBranches: Branch[], courses: Course[]) {
  const result = nearbyBranches.map(normalizeCoordinates);
  const evidenceByIndex = new Map<number, BranchEvidence>();
  const aliasToIndex = new Map<string, number>();
  const physicalKeyToIndex = new Map<string, number>();

  const indexBranch = (branch: Branch, index: number) => {
    aliasesFor(branch).forEach((id) => aliasToIndex.set(id, index));
    const physicalKey = physicalBranchKey(branch);
    if (physicalKey && !physicalKeyToIndex.has(physicalKey)) physicalKeyToIndex.set(physicalKey, index);
  };
  result.forEach(indexBranch);

  courses.forEach((course) => {
    if (!course.branch || !branchCoordinates(course.branch)) return;
    const candidate = normalizeCoordinates(course.branch);
    const candidateAliases = aliasesFor(candidate, course.branch_id);
    let index = candidateAliases
      .map((id) => aliasToIndex.get(id))
      .find((value): value is number => value !== undefined);
    if (index === undefined) {
      const physicalKey = physicalBranchKey(candidate);
      index = physicalKey ? physicalKeyToIndex.get(physicalKey) : undefined;
    }

    if (index === undefined) {
      index = result.length;
      result.push({
        ...candidate,
        id: candidate.id || course.branch_id || `course-branch-${course.id}`,
        branch_ids: candidateAliases,
        providers: mergeValues(candidate.providers, [candidate.provider, course.provider]),
      });
      indexBranch(result[index], index);
    }

    const current = result[index];
    const categories = courseCategories(course);
    const serviceGroups = courseServiceGroups(course);
    result[index] = {
      ...current,
      branch_ids: mergeValues(current.branch_ids, candidateAliases),
      providers: mergeValues(current.providers, [candidate.provider, course.provider]),
      collection_categories: mergeValues(current.collection_categories, [
        ...(candidate.collection_categories || []),
        ...categories,
      ]),
      service_groups: mergeValues(current.service_groups, [
        ...(candidate.service_groups || []),
        ...serviceGroups,
      ]),
      primary_collection_category:
        current.primary_collection_category
        || candidate.primary_collection_category
        || categories[0]
        || null,
      primary_service_group:
        current.primary_service_group
        || candidate.primary_service_group
        || serviceGroups[0]
        || null,
    };
    candidateAliases.forEach((id) => aliasToIndex.set(id, index));

    const evidence = evidenceByIndex.get(index) ?? newEvidence();
    evidence.courseIds.add(course.id);
    if (course.status === 'OPEN') evidence.openCourseIds.add(course.id);
    recordEvidence(evidence.categories, categories, course.id);
    recordEvidence(evidence.serviceGroups, serviceGroups, course.id);
    evidenceByIndex.set(index, evidence);
  });

  evidenceByIndex.forEach((evidence, index) => {
    const branch = result[index];
    const categoryCounts = mergeEvidenceCounts(branch.category_counts, evidence.categories);
    const serviceGroupCounts = mergeEvidenceCounts(branch.service_group_counts, evidence.serviceGroups);
    result[index] = {
      ...branch,
      course_count: Math.max(Number(branch.course_count || 0), evidence.courseIds.size),
      active_course_count: Math.max(Number(branch.active_course_count || 0), evidence.courseIds.size),
      open_course_count: Math.max(Number(branch.open_course_count || 0), evidence.openCourseIds.size),
      category_counts: categoryCounts,
      service_group_counts: serviceGroupCounts,
      collection_categories: mergeValues(branch.collection_categories, [...evidence.categories.keys()]),
      service_groups: mergeValues(branch.service_groups, [...evidence.serviceGroups.keys()]),
    };
  });

  return result;
}

function sharesBranchIdentity(left: Branch, right: Branch) {
  const rightAliases = new Set(aliasesFor(right));
  if (aliasesFor(left).some((id) => rightAliases.has(id))) return true;
  const leftKey = physicalBranchKey(left);
  return Boolean(leftKey && leftKey === physicalBranchKey(right));
}

/** Current API rows are authoritative; only absent, explicitly pinned rows survive a refresh. */
export function replaceNearbyBranchSnapshot(
  previous: Branch[],
  current: Branch[],
  pinnedBranchIds: Iterable<string>,
) {
  const pinned = new Set(pinnedBranchIds);
  const next = current.map(normalizeCoordinates);
  previous.forEach((branch) => {
    if (!aliasesFor(branch).some((id) => pinned.has(id))) return;
    if (next.some((candidate) => sharesBranchIdentity(branch, candidate))) return;
    next.push(normalizeCoordinates(branch));
  });
  return next;
}

export function mapBranchCourseCount(
  branch: Branch,
  mapMode: MapMode,
  resultCourseCounts?: Record<string, number>,
) {
  const resultCount = branchSourceIds(branch).reduce(
    (sum, branchId) => sum + Number(resultCourseCounts?.[branchId] || 0),
    0,
  );
  return Math.max(branchMapModeCourseCount(branch, mapMode), resultCount);
}

type SelectMapBranchesOptions = {
  branches: Branch[];
  userLocation: UserLocation;
  radiusKm: number;
  providerFilters: string[];
  categoryFilters: string[];
  categoryFilterActive?: boolean;
  mapMode: MapMode;
  resultCourseCounts?: Record<string, number>;
};

/** Single marker/list eligibility contract shared by every frontend map surface. */
export function selectMapMarkerBranches({
  branches,
  userLocation,
  radiusKm,
  providerFilters,
  categoryFilters,
  categoryFilterActive = true,
  mapMode,
  resultCourseCounts,
}: SelectMapBranchesOptions) {
  const providerSet = new Set(providerFilters);
  const categorySet = new Set(categoryFilters.map((value) => value.trim().toLocaleLowerCase('ko-KR')));

  return branches.filter((branch) => {
    if (!branchCoordinates(branch)) return false;
    if (distanceKm(userLocation, branch) > radiusKm) return false;

    const resultCount = branchSourceIds(branch).reduce(
      (sum, branchId) => sum + Number(resultCourseCounts?.[branchId] || 0),
      0,
    );
    const hasCurrentResultEvidence = resultCount > 0;
    const displayCount = Math.max(branchMapModeCourseCount(branch, mapMode), resultCount);
    if (displayCount <= 0 && !isFacilityInfoBranch(branch)) return false;

    // The course API already applied scope/provider/category filters. Its branch
    // evidence wins when nearby aggregation is missing or lags behind it.
    if (hasCurrentResultEvidence) return true;

    if (!branchProviders(branch).some((provider) => providerSet.has(provider))) return false;
    if (!branchMatchesMapMode(branch, mapMode)) return false;
    if (mapMode === 'provider' || !categoryFilterActive) return true;
    return branchCategories(branch).some(
      (category) => categorySet.has(category.trim().toLocaleLowerCase('ko-KR')),
    );
  });
}
